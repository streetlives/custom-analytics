from enum import Enum
from fastapi import FastAPI
import datetime
import psycopg2
import os
from ga4 import fetch_ga4_report
import pandas as pd
from json import loads

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

app = FastAPI()

@app.get("/analytics-data")
async def analytics_data(start_date: datetime.date, end_date: datetime.date, geometry_type: GeometryEnum):
    ga4_report = fetch_ga4_report(start_date, end_date)
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
            return { r.name: int(r['totalUsers']) for r in agg_result.iloc}
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
            return { int(r.name): int(r['totalUsers']) for r in agg_result.iloc }

@app.get("/geojson-geometries")
async def analytics_data(geometry_type: GeometryEnum):
    with conn.cursor() as cur:
        if geometry_type == GeometryEnum.neighborhood:
            cur.execute('''
                select neighborhood, borough, ST_AsGeoJSON(geometry)::json from nyc_neighborhood_geometries
            ''')
        else:
            cur.execute('''
                select district_id, ST_AsGeoJSON(geometry)::json from nyc_districts
                where type = %s 
            ''', (geometry_type.value,))

        return cur.fetchall()
