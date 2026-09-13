{#
  Collapse a company name to a comparison key.

  This exists because manufacturer names in CMS are not normalised: 'AbbVie Inc.' and
  'ABBVIE INC.' are distinct rows. Measured on the general file, 2,472 written names
  resolve to 2,269 real entities -- 8.2% inflation. That bug understated AbbVie by
  roughly half in a sponsor table we had already circulated.

  Every model that groups by company MUST group by this, never by the raw name.
#}
{% macro normalize_entity(col) %}
  lower(regexp_replace(
    regexp_replace({{ col }}, '\b(INC|INCORPORATED|LLC|L\.L\.C|LTD|CORP|CORPORATION|CO|COMPANY|USA|US|PLC|GMBH|AG|SA|LP|LLP|PC|PA|THE|DBA)\b', '', 'gi'),
    '[^A-Za-z0-9]', '', 'g'))
{% endmacro %}

{#
  Entities that appear as research "sites" but are not sites.
  Advarra is a central IRB and shows up with up to 117 principal investigators --
  it would top any unfiltered site ranking. WCG/Copernicus are the same problem.
#}
{% macro is_not_a_real_site(col) %}
  (   lower({{ col }}) LIKE '%advarra%'
   OR lower({{ col }}) LIKE '%copernicus%'
   OR lower({{ col }}) LIKE '%wirb%'
   OR lower({{ col }}) LIKE '%institutional review%'
   OR lower({{ col }}) LIKE '%western irb%'
   OR {{ col }} ~ '^[0-9]+$'
   OR length(trim({{ col }})) < 3 )
{% endmacro %}
