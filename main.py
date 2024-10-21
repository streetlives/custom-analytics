import secrets
from enum import Enum
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import datetime
import psycopg2
import os
from ga4 import fetch_total_users_for_page_path, fetch_geolocation_events_from_ga4
import pandas as pd
from json import loads
import json
from fastapi.staticfiles import StaticFiles
import re
import numpy as np
from functools import partial
from urllib.parse import urlparse, parse_qs
from typing import Annotated


security = HTTPBasic()

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

locations_re = re.compile(r'^/locations/(?P<slug>[^/]+)')

def format_value(value, geometry_enum: GeometryEnum):
    return value if geometry_enum.value == 'neighborhood' else int(value)

category_paths = (
    'food',
    'shelters-housing',
    'clothing',
    'personal-care',
    'health-care',
    'other-services',
)

database_category_map = {
    'Food': 'food',
    'Shelter': 'shelters-housing',
    'Clothing': 'clothing',
    'Personal Care': 'personal-care',
    'Health': 'health-care',
    'Other service': 'other-services',

}

def fetch_ratio_of_service_categories_for_locations():
    # if it's location details page, then find location from slug
    # find services at location
    # find taxonomy of services
    # find parent taxonomy of services
    # find percentage of services in parent taxonomy
    # these are the weights
    with conn.cursor() as cur:
        cur.execute('''
            select slug, case when t.parent_name is not null then t.parent_name else t.name end as service_category, count(1) from 
            (
                select slug, location_id from location_slug_redirects
                union 
                select slug, id as location_id from locations
            ) l
            inner join service_at_locations sal on sal.location_id = l.location_id
            inner join service_taxonomy st on st.service_id = sal.service_id
            inner join taxonomies t on t.id = st.taxonomy_id
            group by slug, service_category
            order by slug
        ''')
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([{'slug': row[0], 'category': database_category_map[row[1]], 'count': row[2]} for row in rows])
        count_by_slug = df.groupby('slug').sum()
        indexed_df = df.set_index(['slug'])
        for slug, row in indexed_df.iterrows():
            count = row['count']
            total_count = count_by_slug.loc[slug]['count']
            indexed_df.loc[slug, 'percentage'] = count / total_count
        return indexed_df


def row_to_category_weights(count_of_service_categories_df, x):
    index, row = x
    pathname = row['pathname']
    previous_params_route = row['previousParamsRoute']
    path_components = pathname.split('/')
    first_component = path_components[1]
    if first_component in category_paths:
        return {'index' : index, first_component :  1 }
    elif previous_params_route in category_paths:
        return {'index' : index, previous_params_route: 1}
    elif first_component == 'locations' and len(path_components) == 3:
        # TODO extract the slug
        slug = path_components[2]
        row = count_of_service_categories_df[count_of_service_categories_df.index == slug][['category', 'percentage']].set_index('category')['percentage'].to_dict()
        return {
            'index': index,
            **row
        }

    return {'index' : index, 'unknown': 1}


def get_current_username(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    current_username_bytes = credentials.username.encode("utf8")
    correct_username_bytes = os.environ.get('API_USERNAME').encode("utf8")
    is_correct_username = secrets.compare_digest(
        current_username_bytes, correct_username_bytes
    )
    current_password_bytes = credentials.password.encode("utf8")
    correct_password_bytes = os.environ.get('API_PASSWORD').encode("utf8")
    is_correct_password = secrets.compare_digest(
        current_password_bytes, correct_password_bytes
    )
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.get("/geolocation-service-category-analytics")
async def geolocation_service_category_analytics(
    start_date: datetime.date, 
    end_date: datetime.date, 
    geometry_type: GeometryEnum, 
    username: Annotated[str, Depends(get_current_username)],
):
    count_of_service_categories_df = fetch_ratio_of_service_categories_for_locations()
    category_df = pd.DataFrame(fetch_geolocation_events_from_ga4(start_date, end_date, with_previous_params_route=True))
    # TODO: optimize the lookup
    category_weights_df = pd.DataFrame(
        list(
            map(
                partial(row_to_category_weights, count_of_service_categories_df), 
                category_df.iterrows()
            )
        )
    ).set_index('index')
    category_df.join(category_weights_df).to_csv('category_weights_df.csv')
    lookup_map = {}
    for index, row in category_df.iterrows():
        district = row[geometry_type.value]
        if pd.isna(district):
            continue
        district = format_value(district, geometry_type)
        num_geolocation_events = int(row['numGeolocationEvents'])
        category_weights = category_weights_df.loc[index].to_dict()
        if district in lookup_map:
            for category, weight in category_weights.items():
                    if not pd.isna(weight):
                        if category in lookup_map[district]:
                            lookup_map[district][category] += weight * num_geolocation_events 
                        else:
                            lookup_map[district][category] = weight * num_geolocation_events 

            lookup_map[district][category] = num_geolocation_events
        else:
            lookup_map[district] = { category: weight * num_geolocation_events for category, weight in category_weights.items() if not pd.isna(weight)}
    return lookup_map       


@app.get("/sankey")
async def location_analytics(
    start_date: datetime.date, 
    end_date: datetime.date, 
    geolocation_geometry_type: GeometryEnum, 
    location_details_geometry_type: GeometryEnum,
    username: Annotated[str, Depends(get_current_username)],
):
    ga4_report = fetch_geolocation_events_from_ga4(start_date, end_date)

    geolocation_df = pd.DataFrame([
        {
            'slug': locations_re.match(row['pathname']).group('slug'), 
            f'geolocation_{geolocation_geometry_type.value}': format_value(row[geolocation_geometry_type.value], geolocation_geometry_type),
            'numGeolocationEvents': row['numGeolocationEvents']
        } for row in ga4_report \
            if locations_re.match(row['pathname']) and row[geolocation_geometry_type.value] is not None
    ]).set_index('slug')
    slugs = geolocation_df.index
    with conn.cursor() as cur:
        cur.execute('''
            select slug, locations_geocoded_metadata.* from locations 
            inner join locations_geocoded_metadata on locations.id = locations_geocoded_metadata.location_id
            where slug in %s
        ''', (tuple(slugs),))
        location_metadata_by_slug_df = pd.DataFrame([ {
            'slug': row[0],
            'location_id': row[1],
            'neighborhood': row[2],
            'borough': row[3],
            'school': row[4],
            'congressional': row[5],
            'community': row[6],
        } for row in cur.fetchall() ])\
            .set_index('slug')

        location_metadata_by_slug_df = location_metadata_by_slug_df[[location_details_geometry_type.value]].\
            add_prefix(f'location_details_')

        joined_df = geolocation_df.join(location_metadata_by_slug_df)

        geolocation_lookup_map = {}
        location_details_lookup_map = {}

        filtered_joined_df = joined_df[
            ~joined_df[f'geolocation_{geolocation_geometry_type.value}'].isna() & \
                ~joined_df[f'location_details_{location_details_geometry_type.value}'].isna()
        ]
        for slug, row in filtered_joined_df.iterrows():
            geolocation_value = format_value(row[f'geolocation_{geolocation_geometry_type.value}'], geolocation_geometry_type)
            location_details_value = format_value(row[f'location_details_{location_details_geometry_type.value}'], location_details_geometry_type)
            event_count = row['numGeolocationEvents']

            # populate geolocation_lookup_map
            if geolocation_value in geolocation_lookup_map:
                _location_details_lookup_map = geolocation_lookup_map[geolocation_value]
                if location_details_value in _location_details_lookup_map:
                    _location_details_lookup_map[location_details_value] += event_count
                else:
                    _location_details_lookup_map[location_details_value] = event_count
            else:
                geolocation_lookup_map[geolocation_value] = { location_details_value: event_count }

            # populate location_details_lookup_map
            if location_details_value in location_details_lookup_map:
                _geolocation_lookup_map = location_details_lookup_map[location_details_value]
                if geolocation_value in _geolocation_lookup_map:
                    _geolocation_lookup_map[geolocation_value] += event_count
                else:
                    _geolocation_lookup_map[geolocation_value] = event_count
            else:    
                location_details_lookup_map[location_details_value] = { geolocation_value: event_count }


        return {
            'geolocationLookup': geolocation_lookup_map,
            'locationDetailsLookup': location_details_lookup_map
        }


@app.get("/location-analytics")
async def location_analytics(
    start_date: datetime.date, 
    end_date: datetime.date, 
    analytics_metric_type: AnalyticsMetricEnum,
    username: Annotated[str, Depends(get_current_username)],
):
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
async def analytics_data(
    start_date: datetime.date, 
    end_date: datetime.date, 
    geometry_type: GeometryEnum, 
    analytics_metric_type: AnalyticsMetricEnum,
    username: Annotated[str, Depends(get_current_username)],
):
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
async def analytics_data(
    geometry_type: GeometryEnum,
    username: Annotated[str, Depends(get_current_username)],
):
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

@app.get("/search-terms")
async def search_terms(
    start_date: datetime.date, 
    end_date: datetime.date,
    username: Annotated[str, Depends(get_current_username)],
):
    ga4_report = fetch_total_users_for_page_path(start_date, end_date, dimension="pagePathPlusQueryString")
    results = {}
    for row in ga4_report:
        total_users = int(row['totalUsers'])
        parsed_url = urlparse(row["pagePathPlusQueryString"])
        if parsed_url.query:
            parsed_query = parse_qs(parsed_url.query)
            if 'search' in parsed_query:
                search_text = parsed_query['search'][0]
                if search_text in results:
                    results[search_text] += total_users
                else:
                    results[search_text] = total_users
    return results

app.mount("/", StaticFiles(directory="static", html=True), name="static")
