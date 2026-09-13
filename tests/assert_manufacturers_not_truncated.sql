-- BUG THIS CATCHES: the CRM stored only top manufacturers per contact, so a TMS query
-- returned zero for Neuronetics ($2.8M in source) and LivaNova -- the largest psychiatry
-- spender at $55.2M -- was entirely invisible. Both must be present and reachable here.
with probe as (
    select
      count(*) filter (where lower(sponsor_name) like '%neuronetic%') as neuronetics,
      count(*) filter (where lower(sponsor_name) like '%livanova%')   as livanova
    from {{ ref('mart_npi_manufacturers') }}
)
select * from probe where neuronetics = 0 or livanova = 0
