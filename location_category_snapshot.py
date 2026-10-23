"""Append-only location -> service -> parent category snapshots (issue #6).

Each run captures the full current state of the production directory in a
single read-only statement, so every row shares one snapshot_observed_at.
A snapshot is evidence of database state at that instant only: never treat it
as valid for earlier times or for the gap until the next capture.

Usage:
    python location_category_snapshot.py --dry-run          # query + QA only
    python location_category_snapshot.py --csv out.csv      # also write raw CSV
    python location_category_snapshot.py                    # append to BigQuery

BigQuery target comes from SNAPSHOT_BQ_PROJECT / SNAPSHOT_BQ_DATASET; the
service account comes from GCP_CREDENTIALS (same variable ga4.py uses) and
needs BigQuery Data Editor on the dataset plus Job User on the project.

Schedule daily (minimum) through the same egress as the web dyno, e.g.
Heroku Scheduler: bin/proximo python location_category_snapshot.py

Snapshot rows are loaded before the run is logged, so consumers must only use
rows whose run_id has status = 'succeeded' in location_category_snapshot_runs.
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import sys
import traceback
import uuid

from dotenv import load_dotenv
import psycopg2

load_dotenv()

SCHEMA_VERSION = 1
SNAPSHOTS_TABLE = 'location_category_snapshots'
RUNS_TABLE = 'location_category_snapshot_runs'

# Same statement validated against the 2026-10-14 exports and archived as the
# 2026-10-15 09:24:59 EDT baseline. Keep it in sync with the methodology doc.
SNAPSHOT_SQL = '''
WITH capture AS (
    SELECT statement_timestamp() AS snapshot_observed_at
),
all_aliases AS (
    SELECT l.id AS location_id, l.slug, 'canonical'::text AS slug_kind, 0 AS priority
    FROM locations l
    WHERE NULLIF(BTRIM(l.slug), '') IS NOT NULL
    UNION ALL
    SELECT r.location_id, r.slug, 'redirect'::text AS slug_kind, 1 AS priority
    FROM location_slug_redirects r
    WHERE NULLIF(BTRIM(r.slug), '') IS NOT NULL
),
aliases AS (
    SELECT DISTINCT ON (location_id, LOWER(BTRIM(slug)))
        location_id, slug, slug_kind
    FROM all_aliases
    ORDER BY location_id, LOWER(BTRIM(slug)), priority, slug
),
slug_collisions AS (
    SELECT LOWER(BTRIM(slug)) AS slug_key, COUNT(DISTINCT location_id) AS mapped_location_count
    FROM aliases
    GROUP BY LOWER(BTRIM(slug))
),
services_with_hours AS (
    SELECT DISTINCT service_id FROM holiday_schedules
),
closed_locations AS (
    SELECT DISTINCT location_id FROM event_related_info WHERE event = 'CLOSURE'
),
service_categories AS (
    SELECT DISTINCT
        sal.location_id,
        sal.service_id,
        t.id AS taxonomy_id,
        t.name AS taxonomy_name,
        t.parent_name AS taxonomy_parent_name,
        COALESCE(NULLIF(BTRIM(t.parent_name), ''), t.name) AS raw_parent_category,
        (h.service_id IS NOT NULL) AS has_holiday_schedule
    FROM service_at_locations sal
    LEFT JOIN service_taxonomy st ON st.service_id = sal.service_id
    LEFT JOIN taxonomies t ON t.id = st.taxonomy_id
    LEFT JOIN services_with_hours h ON h.service_id = sal.service_id
)
SELECT
    c.snapshot_observed_at,
    (c.snapshot_observed_at AT TIME ZONE 'America/New_York')::date AS snapshot_date_ny,
    l.id AS location_id,
    l.slug AS canonical_slug,
    a.slug AS observed_path_slug,
    CASE WHEN a.slug IS NOT NULL THEN '/locations/' || LOWER(BTRIM(a.slug)) ELSE NULL END AS normalized_page_path,
    a.slug_kind,
    (COALESCE(sc.mapped_location_count, 0) > 1) AS slug_collision,
    COALESCE(l.hidden_from_search, FALSE) AS hidden_from_search,
    (cl.location_id IS NOT NULL) AS has_closure_event,
    s.service_id,
    s.taxonomy_id,
    s.taxonomy_name,
    s.taxonomy_parent_name,
    s.raw_parent_category,
    CASE
        WHEN s.raw_parent_category = 'Shelter' THEN 'Shelter & Housing'
        WHEN s.raw_parent_category = 'Other service' THEN 'Other Services'
        ELSE s.raw_parent_category
    END AS reporting_parent_category,
    COALESCE(s.has_holiday_schedule, FALSE) AS service_has_holiday_schedule
FROM locations l
CROSS JOIN capture c
LEFT JOIN aliases a ON a.location_id = l.id
LEFT JOIN slug_collisions sc ON sc.slug_key = LOWER(BTRIM(a.slug))
LEFT JOIN closed_locations cl ON cl.location_id = l.id
LEFT JOIN service_categories s ON s.location_id = l.id
ORDER BY l.id, a.slug_kind, a.slug, s.service_id, s.taxonomy_name
'''

# (name, BigQuery type) in SNAPSHOT_SQL column order.
SNAPSHOT_COLUMNS = [
    ('snapshot_observed_at', 'TIMESTAMP'),
    ('snapshot_date_ny', 'DATE'),
    ('location_id', 'STRING'),
    ('canonical_slug', 'STRING'),
    ('observed_path_slug', 'STRING'),
    ('normalized_page_path', 'STRING'),
    ('slug_kind', 'STRING'),
    ('slug_collision', 'BOOL'),
    ('hidden_from_search', 'BOOL'),
    ('has_closure_event', 'BOOL'),
    ('service_id', 'STRING'),
    ('taxonomy_id', 'STRING'),
    ('taxonomy_name', 'STRING'),
    ('taxonomy_parent_name', 'STRING'),
    ('raw_parent_category', 'STRING'),
    ('reporting_parent_category', 'STRING'),
    ('service_has_holiday_schedule', 'BOOL'),
]
COLUMN_NAMES = [name for name, _ in SNAPSHOT_COLUMNS]
# Columns that identify the observation rather than the directory state.
OBSERVATION_COLUMNS = {'snapshot_observed_at', 'snapshot_date_ny'}

RUN_COLUMNS = [
    ('run_id', 'STRING'),
    ('schema_version', 'INT64'),
    ('status', 'STRING'),
    ('started_at', 'TIMESTAMP'),
    ('finished_at', 'TIMESTAMP'),
    ('snapshot_observed_at', 'TIMESTAMP'),
    ('previous_run_id', 'STRING'),
    ('summary_json', 'STRING'),
    ('changes_json', 'STRING'),
    ('error', 'STRING'),
]


def connect():
    conn = psycopg2.connect(
        dbname=os.environ.get('DATABASE_NAME'),
        user=os.environ.get('DATABASE_USER'),
        password=os.environ.get('DATABASE_PASSWORD'),
        host=os.environ.get('DATABASE_HOST'),
        port=os.environ.get('DATABASE_PORT'),
    )
    conn.set_session(readonly=True)
    return conn


def fetch_snapshot(conn):
    with conn.cursor() as cur:
        cur.execute(SNAPSHOT_SQL)
        names = [d[0] for d in cur.description]
        if names != COLUMN_NAMES:
            raise RuntimeError('Unexpected snapshot columns: %s' % names)
        rows = [dict(zip(names, row)) for row in cur.fetchall()]
    conn.rollback()
    for row in rows:
        for key in ('location_id', 'service_id', 'taxonomy_id'):
            if row[key] is not None:
                row[key] = str(row[key])
    return rows


def state_fingerprint(rows):
    """SHA-256 of the directory state, ignoring the observation timestamp."""
    lines = sorted(
        json.dumps([row[c] for c in COLUMN_NAMES if c not in OBSERVATION_COLUMNS], default=str)
        for row in rows
    )
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()


def summarize(rows):
    observed = {row['snapshot_observed_at'] for row in rows}
    if len(observed) != 1:
        raise RuntimeError('Snapshot rows have %d distinct observation times' % len(observed))
    locations = {row['location_id'] for row in rows}
    pairs = {(row['location_id'], row['service_id']) for row in rows if row['service_id']}
    scheduled_pairs = {
        (row['location_id'], row['service_id'])
        for row in rows if row['service_id'] and row['service_has_holiday_schedule']
    }
    memberships = {
        (row['location_id'], row['normalized_page_path'])
        for row in rows if row['normalized_page_path']
    }
    located_with_services = {loc for loc, _ in pairs}
    located_with_scheduled = {loc for loc, _ in scheduled_pairs}
    return {
        'snapshot_observed_at': next(iter(observed)).isoformat(),
        'schema_version': SCHEMA_VERSION,
        'row_count': len(rows),
        'location_count': len(locations),
        'location_service_pair_count': len(pairs),
        'scheduled_location_service_pair_count': len(scheduled_pairs),
        'locations_without_services': len(locations - located_with_services),
        'locations_with_services_but_no_hours': len(located_with_services - located_with_scheduled),
        'distinct_path_count': len({path for _, path in memberships}),
        'location_path_membership_count': len(memberships),
        'canonical_membership_count': len({
            (row['location_id'], row['normalized_page_path'])
            for row in rows if row['slug_kind'] == 'canonical'
        }),
        'redirect_membership_count': len({
            (row['location_id'], row['normalized_page_path'])
            for row in rows if row['slug_kind'] == 'redirect'
        }),
        'collision_paths': sorted({row['normalized_page_path'] for row in rows if row['slug_collision']}),
        'services_without_taxonomy': len({
            (row['location_id'], row['service_id'])
            for row in rows if row['service_id'] and row['taxonomy_id'] is None
        }),
        'state_sha256': state_fingerprint(rows),
    }


def location_state(rows):
    """Per-location attributes used to report what changed between captures."""
    state = {}
    for row in rows:
        loc = state.setdefault(row['location_id'], {
            'canonical_slug': row['canonical_slug'],
            'hidden_from_search': row['hidden_from_search'],
            'has_closure_event': row['has_closure_event'],
            'paths': set(),
            'services': set(),
            'scheduled_services': set(),
            'parent_categories': set(),
        })
        if row['normalized_page_path']:
            loc['paths'].add(row['normalized_page_path'])
        if row['service_id']:
            loc['services'].add(row['service_id'])
            if row['service_has_holiday_schedule']:
                loc['scheduled_services'].add(row['service_id'])
            loc['parent_categories'].add(row['reporting_parent_category'] or 'UNKNOWN')
    return state


def diff_snapshots(previous_rows, current_rows):
    previous, current = location_state(previous_rows), location_state(current_rows)
    changed = {}
    for location_id in sorted(previous.keys() & current.keys()):
        before, after = previous[location_id], current[location_id]
        fields = sorted(key for key in after if before[key] != after[key])
        if fields:
            changed[location_id] = fields
    field_counts = {}
    for fields in changed.values():
        for field in fields:
            field_counts[field] = field_counts.get(field, 0) + 1
    return {
        'added_locations': sorted(current.keys() - previous.keys()),
        'removed_locations': sorted(previous.keys() - current.keys()),
        'changed_location_count': len(changed),
        'changed_field_counts': field_counts,
        'changed_locations': changed,
    }


def write_csv(rows, path):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMN_NAMES)
        writer.writeheader()
        writer.writerows(rows)


class BigQueryStore:
    def __init__(self, project, dataset):
        from google.cloud import bigquery
        from google.oauth2 import service_account

        self.bigquery = bigquery
        credentials = None
        if 'GCP_CREDENTIALS' in os.environ:
            credentials = service_account.Credentials.from_service_account_info(
                json.loads(os.environ['GCP_CREDENTIALS'])
            )
        self.client = bigquery.Client(project=project, credentials=credentials)
        self.dataset = '%s.%s' % (project, dataset)
        self.snapshots_table = '%s.%s' % (self.dataset, SNAPSHOTS_TABLE)
        self.runs_table = '%s.%s' % (self.dataset, RUNS_TABLE)

    def _schema(self, columns):
        return [self.bigquery.SchemaField(name, kind) for name, kind in columns]

    def ensure_tables(self):
        snapshots = self.bigquery.Table(
            self.snapshots_table,
            schema=self._schema([('run_id', 'STRING'), ('schema_version', 'INT64')] + SNAPSHOT_COLUMNS),
        )
        snapshots.time_partitioning = self.bigquery.TimePartitioning(field='snapshot_date_ny')
        self.client.create_table(snapshots, exists_ok=True)
        self.client.create_table(
            self.bigquery.Table(self.runs_table, schema=self._schema(RUN_COLUMNS)),
            exists_ok=True,
        )

    def previous_snapshot(self):
        """Rows of the most recent successful run, or (None, [])."""
        query = (
            'SELECT run_id FROM `%s` WHERE status = "succeeded" '
            'ORDER BY snapshot_observed_at DESC LIMIT 1' % self.runs_table
        )
        found = list(self.client.query(query).result())
        if not found:
            return None, []
        run_id = found[0]['run_id']
        job = self.client.query(
            'SELECT %s FROM `%s` WHERE run_id = @run_id' % (', '.join(COLUMN_NAMES), self.snapshots_table),
            job_config=self.bigquery.QueryJobConfig(
                query_parameters=[self.bigquery.ScalarQueryParameter('run_id', 'STRING', run_id)]
            ),
        )
        return run_id, [dict(row.items()) for row in job.result()]

    def _load(self, table, columns, rows):
        job = self.client.load_table_from_json(
            [json_ready(row) for row in rows],
            table,
            job_config=self.bigquery.LoadJobConfig(
                schema=self._schema(columns),
                write_disposition=self.bigquery.WriteDisposition.WRITE_APPEND,
            ),
        )
        job.result()

    def append_snapshot(self, run_id, rows):
        self._load(
            self.snapshots_table,
            [('run_id', 'STRING'), ('schema_version', 'INT64')] + SNAPSHOT_COLUMNS,
            [dict(row, run_id=run_id, schema_version=SCHEMA_VERSION) for row in rows],
        )

    def log_run(self, run):
        self._load(self.runs_table, RUN_COLUMNS, [run])


def json_ready(row):
    return {
        key: value.isoformat() if isinstance(value, (datetime.date, datetime.datetime)) else value
        for key, value in row.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='query and print QA summary; do not write to BigQuery')
    parser.add_argument('--csv', help='also write the raw snapshot rows to this CSV path')
    args = parser.parse_args()

    store = None
    if not args.dry_run:
        project, dataset = os.environ.get('SNAPSHOT_BQ_PROJECT'), os.environ.get('SNAPSHOT_BQ_DATASET')
        if not project or not dataset:
            parser.error('SNAPSHOT_BQ_PROJECT and SNAPSHOT_BQ_DATASET must be set (or use --dry-run)')
        store = BigQueryStore(project, dataset)
        store.ensure_tables()

    run = {
        'run_id': str(uuid.uuid4()),
        'schema_version': SCHEMA_VERSION,
        'status': 'failed',
        'started_at': datetime.datetime.now(datetime.timezone.utc),
        'finished_at': None,
        'snapshot_observed_at': None,
        'previous_run_id': None,
        'summary_json': None,
        'changes_json': None,
        'error': None,
    }
    try:
        conn = connect()
        try:
            rows = fetch_snapshot(conn)
        finally:
            conn.close()
        summary = summarize(rows)
        run['snapshot_observed_at'] = rows[0]['snapshot_observed_at']
        run['summary_json'] = json.dumps(summary)
        print(json.dumps(summary, indent=2))

        if args.csv:
            write_csv(rows, args.csv)
            print('Wrote %d rows to %s' % (len(rows), args.csv))

        if store:
            previous_run_id, previous_rows = store.previous_snapshot()
            run['previous_run_id'] = previous_run_id
            if previous_rows:
                changes = diff_snapshots(previous_rows, rows)
                run['changes_json'] = json.dumps(changes)
                print('Changes vs run %s: %s' % (previous_run_id, json.dumps({
                    k: v for k, v in changes.items() if k != 'changed_locations'
                })))
            store.append_snapshot(run['run_id'], rows)
            run['status'] = 'succeeded'
    except Exception:
        run['error'] = traceback.format_exc()
        print(run['error'], file=sys.stderr)
    finally:
        run['finished_at'] = datetime.datetime.now(datetime.timezone.utc)
        if store:
            store.log_run(run)

    if store and run['status'] != 'succeeded':
        sys.exit(1)
    if not store and run['error']:
        sys.exit(1)


if __name__ == '__main__':
    main()
