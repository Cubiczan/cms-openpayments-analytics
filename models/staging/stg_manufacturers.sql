-- Every company that made a payment, resolved to ONE row per real entity.
-- The raw name is kept for traceability; entity_key is what everything joins on.
with all_payments as (
    select
        Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name as raw_name,
        Total_Amount_of_Payment_USDollars as amount,
        'general' as payment_type
    from {{ source('cms','op_general') }}
    where Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name <> ''
    union all
    select
        Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name,
        Total_Amount_of_Payment_USDollars,
        'research'
    from {{ source('cms','op_research') }}
    where Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name <> ''
)
select
    {{ normalize_entity('raw_name') }}                     as entity_key,
    min(raw_name)                                          as display_name,
    count(distinct raw_name)                               as name_variants,
    sum(amount)                                            as total_dollars,
    sum(case when payment_type='general'  then amount else 0 end) as general_dollars,
    sum(case when payment_type='research' then amount else 0 end) as research_dollars,
    count(*)                                               as payment_rows
from all_payments
where {{ normalize_entity('raw_name') }} <> ''
group by 1
