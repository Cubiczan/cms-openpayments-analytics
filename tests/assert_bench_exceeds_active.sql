-- BUSINESS-ASSUMPTION MONITOR, not a data-quality test.
--
-- Some analyses depend on there being meaningful untapped investigator capacity: clinicians
-- with established industry relationships who have never been named on a study. Measured
-- like-for-like within the scored psychiatry population: 1,929 untapped bench against 626
-- active investigators -- the bench is roughly 3x the active pool.
--
-- This supersedes an earlier comparison that set untapped PSYCHIATRISTS against ALL
-- behavioural-health investigators in a state. Those are different populations and the
-- comparison was invalid: it made the bench look smaller than the active pool, when measured
-- within one population it is substantially larger. That number had already been published
-- before the error was found, which is the entire argument for putting assumptions into
-- tests rather than into prose.
--
-- Fails if the bench stops being at least as large as the active pool, because that would
-- remove the premise downstream work rests on.
with seg as (
    select
      count(*) filter (where capacity_segment='untapped_bench')      as bench,
      count(*) filter (where capacity_segment='active_investigator') as active
    from {{ ref('mart_psychiatry_bench') }}
)
select bench, active from seg where bench < active
