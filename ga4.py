import datetime
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)

def fetch_ga4_report(start_date: datetime.date, end_date: datetime.date, property_id="403148122"):
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
            Metric(name="activeUsers"),
            Metric(name="screenPageViews"),
            Metric(name="sessions"),
            Metric(name="totalUsers"),
        ],
        date_ranges=[DateRange(start_date=str(start_date), end_date=str(end_date))],
    )
    response = client.run_report(request)

    return [{
        "pagePath": row.dimension_values[0].value,
        "activeUsers": int(row.metric_values[0].value),
        "screenPageViews": int(row.metric_values[1].value),
        "sessions": int(row.metric_values[2].value),
        "totalUsers": int(row.metric_values[3].value),
    } for row in response.rows]

