-- BUG THIS CATCHES: 2,472 written manufacturer names resolve to 2,269 real entities.
-- Grouping by the raw name understated AbbVie by roughly half in a circulated PDF.
-- Fails if two rows in the sponsor mart normalise to the same entity.
select {{ normalize_entity('sponsor') }} as entity_key, count(*) as rows_for_one_entity
from {{ ref('mart_sponsor_ranking') }}
group by 1
having count(*) > 1
