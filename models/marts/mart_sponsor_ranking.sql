-- The corrected sponsor table. Supersedes the ranking in GenAI_Revenue_Thesis.pdf, which
-- grouped by raw name and therefore understated every sponsor that writes its name more
-- than one way -- AbbVie most of all.
select
    display_name                       as sponsor,
    name_variants,
    round(total_dollars)               as total_dollars,
    round(general_dollars)             as general_dollars,
    round(research_dollars)            as research_dollars,
    payment_rows,
    row_number() over (order by total_dollars desc) as rank_overall
from {{ ref('stg_manufacturers') }}
where total_dollars > 0
order by total_dollars desc
