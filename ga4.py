import datetime
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
import os

if 'GCP_CREDENTIALS' in os.environ:
    credentials_json = os.environ.get('GCP_CREDENTIALS')
    with open('credentials.json', 'w') as f:
        f.write(credentials_json)
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'credentials.json'

def parse_int(value):
    return None if value == '(not set)' or value == '' else int(value)

def parse_str(value):
    return None if value == '(not set)' or value == '' else value

def fetch_total_users_for_page_path(start_date: datetime.date, end_date: datetime.date, property_id="403148122", dimension="pagePath"):
    """Runs a simple report on a Google Analytics 4 property."""
    # TODO(developer): Uncomment this variable and replace with your
    #  Google Analytics 4 property ID before running the sample.
    # property_id = "YOUR-GA4-PROPERTY-ID"

    # Using a default constructor instructs the client to use the credentials
    # specified in GOOGLE_APPLICATION_CREDENTIALS environment variable.
    client = BetaAnalyticsDataClient()

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name=dimension),  # Fetch the pagePath dimension
        ],
        metrics=[
            Metric(name="totalUsers"),
        ],
        date_ranges=[DateRange(start_date=str(start_date), end_date=str(end_date))],
    )
    response = client.run_report(request)

    return [{
        dimension: row.dimension_values[0].value,
        "totalUsers": int(row.metric_values[0].value),
    } for row in response.rows]


def fetch_geolocation_events_from_ga4(start_date: datetime.date, end_date: datetime.date, property_id="403148122", with_previous_params_route=False):
    """Runs a simple report on a Google Analytics 4 property."""
    # TODO(developer): Uncomment this variable and replace with your
    #  Google Analytics 4 property ID before running the sample.
    # property_id = "YOUR-GA4-PROPERTY-ID"

    # Using a default constructor instructs the client to use the credentials
    # specified in GOOGLE_APPLICATION_CREDENTIALS environment variable.
    client = BetaAnalyticsDataClient()

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name="customEvent:neighborhood"),
            Dimension(name="customEvent:pathname"),
            Dimension(name="customEvent:communityDistrict"),
            Dimension(name="customEvent:congressionalDistrict"),
            Dimension(name="customEvent:schoolDistrict"),
            Dimension(name="customEvent:state_assembly_district"),
            Dimension(name="customEvent:state_senate_district"),
            #Dimension(name="customEvent:municipal_court_district"),
            Dimension(name="customEvent:city_council_district"),
        ] + ([
            Dimension(name="customEvent:previousParamsRoute"),
        ] if with_previous_params_route else []),
        metrics=[
            Metric(name="keyEvents:geolocation"),
        ],
        date_ranges=[DateRange(start_date=str(start_date), end_date=str(end_date))],
    )
    response = client.run_report(request)

    return [{
        "neighborhood": parse_str(row.dimension_values[0].value),
        "pathname": parse_str(row.dimension_values[1].value),
        "community": parse_int(row.dimension_values[2].value),
        "congressional": parse_int(row.dimension_values[3].value),
        "school": parse_int(row.dimension_values[4].value),
        "state_assembly_district": parse_int(row.dimension_values[5].value),
        "state_senate_district": parse_int(row.dimension_values[6].value),
        #"municipal_court_district": parse_int(row.dimension_values[7].value),
        "city_council_district": parse_int(row.dimension_values[7].value),
        "numGeolocationEvents": float(row.metric_values[0].value),
        **({"previousParamsRoute": parse_str(row.dimension_values[8].value)} if with_previous_params_route else {})
    } for row in response.rows]

if __name__ == '__main__':
    from datetime import date
    from pprint import pprint
    results = fetch_geolocation_events_from_ga4(date(2024, 9, 12), date(2024,10, 2))
    pprint(tuple(sorted(results, key=lambda x:x['numGeolocationEvents'])))
    import pdb
    pdb.set_trace()
