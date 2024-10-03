import datetime
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
import os

if 'GCP_CREDENTIALS' in os.environ():
    credentials_json = os.environ().get('GCP_CREDENTIALS')
    with open('credentials.json', 'w') as f:
        f.write(credentials_json)

def parse_int(value):
    return None if value == '(not set)' or value == '' else int(value)

def parse_str(value):
    return None if value == '(not set)' or value == '' else value

def fetch_total_users_for_page_path(start_date: datetime.date, end_date: datetime.date, property_id="403148122"):
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
            Dimension(name="pagePath"),  # Fetch the pagePath dimension
        ],
        metrics=[
            Metric(name="totalUsers"),
        ],
        date_ranges=[DateRange(start_date=str(start_date), end_date=str(end_date))],
    )
    response = client.run_report(request)

    return [{
        "pagePath": row.dimension_values[0].value,
        "totalUsers": int(row.metric_values[0].value),
    } for row in response.rows]


def fetch_geolocation_events_from_ga4(start_date: datetime.date, end_date: datetime.date, property_id="403148122"):
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
            Dimension(name="customEvent:borough"),
            Dimension(name="customEvent:communityDistrict"), 
            Dimension(name="customEvent:congressionalDistrict"),
            Dimension(name="customEvent:googleBorough"),
            Dimension(name="customEvent:googleNeighborhood"),
            Dimension(name="customEvent:neighborhood"),
            Dimension(name="customEvent:pathname"),
            Dimension(name="customEvent:schoolDistrict"),
            Dimension(name="customEvent:zipCode"),
        ],
        metrics=[
            Metric(name="keyEvents:geolocation"),
        ],
        date_ranges=[DateRange(start_date=str(start_date), end_date=str(end_date))],
    )
    response = client.run_report(request)

    return [{
        "borough": parse_str(row.dimension_values[0].value),
        "community": parse_int(row.dimension_values[1].value),
        "congressional": parse_int(row.dimension_values[2].value),
        "googleBorough": parse_str(row.dimension_values[3].value),
        "googleNeighborhood": parse_str(row.dimension_values[4].value),
        "neighborhood": parse_str(row.dimension_values[5].value),
        "pathname": parse_str(row.dimension_values[6].value),
        "school": parse_int(row.dimension_values[7].value),
        "zipCode": parse_str(row.dimension_values[8].value),
        "numGeolocationEvents": int(row.metric_values[0].value),
    } for row in response.rows]

if __name__ == '__main__':
    from datetime import date
    from pprint import pprint
    results = fetch_geolocation_events_from_ga4(date(2024, 9, 12), date(2024,10, 2))
    pprint(tuple(sorted(results, key=lambda x:x['numGeolocationEvents'])))
    import pdb
    pdb.set_trace()