-- BUG THIS CATCHES: Advarra, a central IRB, appeared as a "research site" with 117
-- principal investigators and would have topped an unranked site list.
--
-- THRESHOLD CALIBRATED 13 SEP. First cut used >60 and failed on Massachusetts General
-- (64 PIs) and Cleveland Clinic (61) -- both genuine large academic centres, not
-- aggregators. So 60 was wrong, not the sites. 100 still catches Advarra at 117 while
-- leaving real AMCs alone. If this fires, check whether it is a new aggregator or a
-- legitimately larger site before widening it again.
select site_name, city, state, investigators, research_dollars
from {{ ref('mart_bh_research_sites') }}
where investigators > 100
