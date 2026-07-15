# kommuner.geojson — provenance

Danish municipality polygons for the consumption choropleth.

- **Source**: Dataforsyningen (DAGI), open data, no key:
  `https://api.dataforsyningen.dk/kommuner?format=geojson` (114 MB raw)
- **Processing** (2026-07-15): shapely `simplify(0.003, preserve_topology=True)`
  (~300 m tolerance), coordinates rounded to 4 decimals, properties trimmed to
  `kode` + `navn` → 0.3 MB.
- **Winding order**: exterior rings re-oriented **clockwise**
  (`shapely orient(sign=-1.0)`). d3-geo — plotly's geo renderer — uses the
  opposite convention of GeoJSON RFC 7946; a counterclockwise exterior ring
  renders as a world-covering fill.
- `kode` is the zero-padded DST municipality code (`"0101"` = 101 København);
  join with `str(municipality_code).zfill(4)`. 99 features = 98 municipalities
  + Christiansø (0411, no consumption data, renders empty).
