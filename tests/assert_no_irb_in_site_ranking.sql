-- BUG THIS CATCHES: Advarra, a central IRB, appeared as the top "research site" with 117
-- principal investigators. It went into a site ranking before anyone noticed.
-- Fails if any excluded-entity pattern survives into the site mart.
select site_name, investigators
from {{ ref('mart_bh_research_sites') }}
where lower(site_name) like '%advarra%'
   or lower(site_name) like '%copernicus%'
   or lower(site_name) like '%wirb%'
   or lower(site_name) like '%institutional review%'
