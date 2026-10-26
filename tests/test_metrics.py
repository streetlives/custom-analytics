from metrics import AnalyticsMetricEnum, metric_fields, aggregate_geolocation_events


def test_geolocation_values_are_labelled_as_events_not_users():
    fields = metric_fields(AnalyticsMetricEnum.geolocation, 5.0)
    assert fields == {
        'count': 5,
        'unit': 'events',
        'metric': 'keyEvents:geolocation',
        'geolocationKeyEvents': 5,
    }
    assert 'totalUsers' not in fields


def test_total_users_for_page_path_keeps_ga4_user_metric():
    fields = metric_fields(AnalyticsMetricEnum.total_users_for_page_path, 3)
    assert fields == {
        'count': 3,
        'unit': 'users',
        'metric': 'totalUsers',
        'totalUsers': 3,
    }


def test_repeated_geolocation_events_from_one_user_count_as_events():
    # One analytics user shares their location three times from the same neighborhood
    # (GA4 rows are pre-aggregated, so that arrives as event counts split across page paths).
    ga4_report = [
        {'neighborhood': 'Harlem', 'pathname': '/locations', 'numGeolocationEvents': 2.0},
        {'neighborhood': 'Harlem', 'pathname': '/food', 'numGeolocationEvents': 1.0},
        {'neighborhood': 'Chelsea', 'pathname': '/locations', 'numGeolocationEvents': 1.0},
    ]
    df = aggregate_geolocation_events(ga4_report, 'neighborhood')
    harlem = metric_fields(AnalyticsMetricEnum.geolocation, df.loc['Harlem', 'numGeolocationEvents'])

    assert harlem['count'] == 3
    assert harlem['geolocationKeyEvents'] == 3
    assert harlem['unit'] == 'events'
    assert 'totalUsers' not in harlem
