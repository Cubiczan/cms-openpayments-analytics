-- Research payments, one row per payment, with the site resolved and non-sites flagged
-- rather than silently dropped. Downstream models filter on is_real_site; keeping the
-- excluded rows visible is how we audit the exclusion instead of trusting it.
select
    cast(Record_ID as varchar)                             as record_id,
    cast(Program_Year as varchar)                          as program_year,
    coalesce(nullif(Noncovered_Recipient_Entity_Name,''),
             nullif(Teaching_Hospital_Name,''),
             nullif(trim(Covered_Recipient_First_Name || ' ' ||
                         Covered_Recipient_Last_Name),''))  as site_name_raw,
    {{ normalize_entity("coalesce(nullif(Noncovered_Recipient_Entity_Name,''),
                                  nullif(Teaching_Hospital_Name,''),'')") }} as site_key,
    Teaching_Hospital_Name <> ''                           as is_teaching_hospital,
    not {{ is_not_a_real_site("coalesce(nullif(Noncovered_Recipient_Entity_Name,''),
                                        nullif(Teaching_Hospital_Name,''),'')") }}
                                                           as is_real_site,
    Recipient_City                                         as city,
    Recipient_State                                        as state,
    nullif(trim(cast(Covered_Recipient_NPI as varchar)),'') as recipient_npi,
    Covered_Recipient_Specialty_1                          as recipient_specialty,
    Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name as sponsor_raw,
    {{ normalize_entity('Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name') }} as sponsor_key,
    Total_Amount_of_Payment_USDollars                      as amount,
    Name_of_Study                                          as study_name,
    ClinicalTrials_Gov_Identifier                          as nct_id,
    Product_Category_or_Therapeutic_Area_1                 as therapeutic_area,
    lower(Product_Category_or_Therapeutic_Area_1) like '%psychiatr%'
      or lower(Product_Category_or_Therapeutic_Area_1) like '%cns%'
      or lower(Product_Category_or_Therapeutic_Area_1) like '%neuroscience%'
      or lower(Name_of_Study) like '%depress%'
      or lower(Name_of_Study) like '%mdd%'
      or lower(Covered_Recipient_Specialty_1) like '%psychiatr%'
                                                           as is_behavioral_health
from {{ source('cms','op_research') }}
