"""Metric definitions for the /location-analytics and /district-neighborhood-analytics endpoints.

The two selectable metrics measure different things and must not be confused:

- geolocation: the GA4 metric `keyEvents:geolocation`, i.e. the number of `geolocation`
  events that GA4 counts as key events. Events are repeatable, so one person sharing their
  location five times contributes 5. This is an EVENT count, never a count of users.
  (It is deliberately the key-event count, not all occurrences of the `geolocation` event,
  i.e. `eventCount` filtered by `eventName`; the two can differ.)

- totalUsersForPagePath: the GA4 metric `totalUsers` for a `/locations/<slug>` page path,
  i.e. distinct users who viewed that page.

Each value in the API response carries `count`, `unit` and `metric`, plus a field named after
the metric (`geolocationKeyEvents` or `totalUsers`). Only the totalUsersForPagePath metric
ever emits `totalUsers`.
"""
from enum import Enum
import pandas as pd


class AnalyticsMetricEnum(str, Enum):
    geolocation = "geolocation"
    total_users_for_page_path = "totalUsersForPagePath"


METRIC_DEFINITIONS = {
    AnalyticsMetricEnum.geolocation: {
        'ga4Metric': 'keyEvents:geolocation',
        'field': 'geolocationKeyEvents',
        'unit': 'events',
    },
    AnalyticsMetricEnum.total_users_for_page_path: {
        'ga4Metric': 'totalUsers',
        'field': 'totalUsers',
        'unit': 'users',
    },
}


def metric_fields(analytics_metric_type: AnalyticsMetricEnum, value) -> dict:
    definition = METRIC_DEFINITIONS[analytics_metric_type]
    count = int(value)
    return {
        'count': count,
        'unit': definition['unit'],
        'metric': definition['ga4Metric'],
        definition['field']: count,
    }


def aggregate_geolocation_events(ga4_report, geometry_key):
    """Sums geolocation key events per geometry. Returns a DataFrame indexed by geometry_key."""
    ga4_report_df = pd.DataFrame(ga4_report)
    return ga4_report_df[["numGeolocationEvents", geometry_key]].set_index(geometry_key).groupby(geometry_key).sum()
