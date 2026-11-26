-- The day a service is for (docs/roadmap/WEB_AND_MOBILE.md): `YYYY-MM-DD`,
-- local, no time. A new service is dated for the coming Sunday and named
-- after it; the name and the date are each editable. Services made before
-- this have none.
ALTER TABLE setlists ADD COLUMN service_date TEXT;
