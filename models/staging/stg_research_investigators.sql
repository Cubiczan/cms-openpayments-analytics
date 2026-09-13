-- op_research carries up to FIVE principal investigators per row in parallel columns.
-- Counting only PI 1 undercounts; this unpivots all five into one row per (payment, PI).
--
-- Every NPI is cast to VARCHAR explicitly. DuckDB infers Parquet types per file, and the
-- three program years disagree: PI 1-2 arrived as BIGINT, PI 3-5 as VARCHAR. Comparing a
-- BIGINT to '' throws at runtime. Normalising types is precisely what staging is for --
-- downstream models should never have to know which year a row came from.
{% set pis = [1,2,3,4,5] %}
with unpivoted as (
    {% for i in pis %}
    select
        cast(Record_ID as varchar)                        as record_id,
        cast(Program_Year as varchar)                     as program_year,
        {{ i }}                                           as pi_slot,
        nullif(trim(cast(Principal_Investigator_{{ i }}_NPI as varchar)),'') as npi,
        cast(Principal_Investigator_{{ i }}_First_Name as varchar) as first_name,
        cast(Principal_Investigator_{{ i }}_Last_Name  as varchar) as last_name,
        cast(Principal_Investigator_{{ i }}_City       as varchar) as city,
        cast(Principal_Investigator_{{ i }}_State      as varchar) as state,
        cast(Principal_Investigator_{{ i }}_Specialty_1 as varchar) as specialty,
        coalesce(nullif(cast(Noncovered_Recipient_Entity_Name as varchar),''),
                 nullif(cast(Teaching_Hospital_Name as varchar),''),'') as site_name_raw,
        cast(Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name as varchar) as sponsor_raw,
        cast(Total_Amount_of_Payment_USDollars as double)  as amount,
        cast(Name_of_Study as varchar)                     as study_name,
        cast(ClinicalTrials_Gov_Identifier as varchar)     as nct_id,
        cast(Product_Category_or_Therapeutic_Area_1 as varchar) as therapeutic_area,
        cast(Covered_Recipient_Specialty_1 as varchar)     as recipient_specialty
    from {{ source('cms','op_research') }}
    where nullif(trim(cast(Principal_Investigator_{{ i }}_NPI as varchar)),'') is not null
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
)
select *,
    {{ normalize_entity('site_name_raw') }}    as site_key,
    {{ normalize_entity('sponsor_raw') }}      as sponsor_key,
    coalesce(lower(therapeutic_area) like '%psychiatr%'
      or lower(therapeutic_area) like '%cns%'
      or lower(therapeutic_area) like '%neuroscience%'
      or lower(study_name) like '%depress%'
      or lower(specialty) like '%psychiatr%'
      or lower(recipient_specialty) like '%psychiatr%', false) as is_behavioral_health
from unpivoted
