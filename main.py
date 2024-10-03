from enum import Enum
from fastapi import FastAPI
import datetime
import psycopg2
import os
from ga4 import fetch_total_users_for_page_path, fetch_geolocation_events_from_ga4
import pandas as pd
from json import loads
import json
from fastapi.staticfiles import StaticFiles
import re


# Connect to your postgres DB
conn = psycopg2.connect(
    dbname=os.environ.get('DATABASE_NAME'),
    user=os.environ.get('DATABASE_USER'),
    password=os.environ.get('DATABASE_PASSWORD'),
    host=os.environ.get('DATABASE_HOST'),
    port=os.environ.get('DATABASE_PORT'),
)

class GeometryEnum(str, Enum):
    community = "community"
    congressional = "congressional"
    school = "school"
    neighborhood = "neighborhood"

class AnalyticsMetricEnum(str, Enum):
    geolocation = "geolocation"
    total_users_for_page_path = "totalUsersForPagePath"

app = FastAPI()

pattern = re.compile(r'POINT\((?P<longitude>-?\d+\.\d+) (?P<latitude>-?\d+\.\d+)\)')
def parse_position(position_string):
    m = pattern.match(position_string)
    return {
        'latitude': float(m.group('latitude')),
        'longitude': float(m.group('longitude'))
    }


@app.get("/location-analytics")
async def location_analytics(start_date: datetime.date, end_date: datetime.date, analytics_metric_type: AnalyticsMetricEnum):
    if analytics_metric_type == AnalyticsMetricEnum.geolocation:
        ga4_report = fetch_geolocation_events_from_ga4(start_date, end_date)
        page_path_key = 'pathname'
        count_of_users_key = 'numGeolocationEvents'
    elif analytics_metric_type == AnalyticsMetricEnum.total_users_for_page_path:
        ga4_report = fetch_total_users_for_page_path(start_date, end_date)
        page_path_key = 'pagePath'
        count_of_users_key = 'totalUsers'

    slugs = [{
        'slug': x[page_path_key].split('/')[2],
        **x
    } for x in ga4_report if len(x[page_path_key].split('/')) == 3 and x[page_path_key].split('/')[1] == 'locations']
    slugs_df = pd.DataFrame(slugs).set_index('slug')
    with conn.cursor() as cur:
        cur.execute('''
            select locations.id, slug, st_astext(position), organizations.name as organization_name from locations
            inner join organizations on locations.organization_id = organizations.id
            where slug in %s
        ''', (tuple([s['slug'] for s in slugs]),))
        database_df = pd.DataFrame([{'id': id, 'slug': slug, 'position': position, 'organization_name' : organization_name} for id, slug, position, organization_name in cur.fetchall()]).set_index('slug')
        joined_df = slugs_df.join(database_df)
        total_count_of_users = joined_df[count_of_users_key].max()
        result = { 
            r.name: {
                'slug': r.name,
                'totalUsers' : int(r[count_of_users_key]), 
                'percentage' : total_count_of_users / r[count_of_users_key],
                'locationId': r['id'], 
                'organizationName': r['organization_name'],
                **parse_position(r['position'])
            } for r in joined_df.iloc if not pd.isna(r['id'])
        }
        print('result', json.dumps(result))
        return result 


@app.get("/district-neighborhood-analytics")
async def analytics_data(start_date: datetime.date, end_date: datetime.date, geometry_type: GeometryEnum, analytics_metric_type: AnalyticsMetricEnum):
    if analytics_metric_type == AnalyticsMetricEnum.geolocation:
        # in this case, we don't need to join anything to the database, because the geo information is already embedded in the GA4 event
        ga4_report = fetch_geolocation_events_from_ga4(start_date, end_date)
        ga4_report_df = pd.DataFrame(ga4_report)
        df = ga4_report_df[["numGeolocationEvents", geometry_type.value]].set_index(geometry_type.value).groupby(geometry_type.value).sum()
        total_count_of_events = df['numGeolocationEvents'].max()
        return { 
            r.name if geometry_type == GeometryEnum.neighborhood else int(r.name): {
                'totalUsers' : int(r['numGeolocationEvents']),
                'percentage' : r['numGeolocationEvents'] / total_count_of_events,
            } for r in df.iloc
        }
    elif analytics_metric_type == AnalyticsMetricEnum.total_users_for_page_path:
        ga4_report = fetch_total_users_for_page_path(start_date, end_date)
        slugs = [{
            'slug': x['pagePath'].split('/')[2],
            **x
        } for x in ga4_report if len(x['pagePath'].split('/')) == 3 and x['pagePath'].split('/')[1] == 'locations']
        slugs_df = pd.DataFrame(slugs).set_index('slug')

        with conn.cursor() as cur:
            if geometry_type == GeometryEnum.neighborhood:
                cur.execute('''
                    select slug, neighborhood from locations
                    inner join nyc_neighborhood_geometries on 
                        ST_Contains(
                            nyc_neighborhood_geometries.geometry, 
                            ST_SetSRID(position,4326)
                        )
                    where slug in %s
                ''', (tuple([s['slug'] for s in slugs]),))
                database_df = pd.DataFrame([{'slug': slug, 'neighborhood': neighborhood} for slug, neighborhood in cur.fetchall()]).set_index('slug')
                joined_df = slugs_df.join(database_df)
                agg_result = joined_df[['neighborhood', 'totalUsers']].groupby('neighborhood').sum()
                total_count_of_users = agg_result['totalUsers'].max()
                print('total_count_of_users ', total_count_of_users )
                return { 
                    r.name: {
                        'totalUsers' : int(r['totalUsers']),
                        'percentage' : r['totalUsers'] / total_count_of_users,
                    } for r in agg_result.iloc
                }
            else:
                cur.execute('''
                    select slug, district_id from locations
                    inner join nyc_districts on 
                        ST_Contains(
                        nyc_districts.geometry, 
                        ST_SetSRID(position,4326)
                        )
                    where type = %s 
                ''', (geometry_type.value,))
                database_df = pd.DataFrame([{'slug': slug, 'district_id': int(district_id)} for slug, district_id in cur.fetchall()]).set_index('slug')
                joined_df = slugs_df.join(database_df)
                agg_result = joined_df[['district_id', 'totalUsers']].groupby('district_id').sum()
                total_count_of_users = agg_result['totalUsers'].max()
                return { 
                    int(r.name): {
                        'totalUsers' : int(r['totalUsers']),
                        'percentage' : r['totalUsers'] / total_count_of_users,
                    } for r in agg_result.iloc 
                }

@app.get("/geojson-geometries")
async def analytics_data(geometry_type: GeometryEnum):
    with conn.cursor() as cur:
        if geometry_type == GeometryEnum.neighborhood:
            cur.execute('''
                select neighborhood, borough, ST_AsGeoJSON(geometry)::json from nyc_neighborhood_geometries
            ''')
            return {
                "type": "FeatureCollection",
                "features": [ { 
                    "type": "Feature", 
                    "properties": { 
                        "id": neighborhood,
                        "neighborhood": neighborhood, 
                        "borough": borough,
                    }, 
                    "geometry": polygon_coordinates
                } for neighborhood, borough, polygon_coordinates in cur.fetchall() ]
            }
        else:
            cur.execute('''
                select district_id, ST_AsGeoJSON(geometry)::json from nyc_districts
                where type = %s 
            ''', (geometry_type.value,))
            return {
                "type": "FeatureCollection",
                "features": [ { 
                    "type": "Feature", 
                    "properties": { 
                        "id": district_id,
                        "districtId": district_id,
                    }, 
                    "geometry": polygon_coordinates
                } for district_id, polygon_coordinates in cur.fetchall() ]
            }

app.mount("/", StaticFiles(directory="static", html=True), name="static")
