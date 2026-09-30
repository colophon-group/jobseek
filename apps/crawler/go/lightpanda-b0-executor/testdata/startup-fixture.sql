-- Disposable CI database only. Alembic owns crawler tables/currency_rate;
-- production reference tables are imported separately and are absent in this
-- image lifecycle fixture. Provide the exact native startup read columns.
CREATE TABLE IF NOT EXISTS public.technology(id bigint PRIMARY KEY,slug text UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS public.occupation(id bigint PRIMARY KEY,slug text UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS public.seniority(id bigint PRIMARY KEY,slug text UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS public.currency_rate(currency text PRIMARY KEY,to_eur numeric NOT NULL);
CREATE TABLE IF NOT EXISTS public.location(id bigint PRIMARY KEY,parent_id bigint,type text NOT NULL,population bigint,languages text[]);
CREATE TABLE IF NOT EXISTS public.location_name(location_id bigint,locale text,name text,is_display boolean);
INSERT INTO public.technology VALUES(4,'python') ON CONFLICT DO NOTHING;
INSERT INTO public.occupation VALUES(41,'software-engineer') ON CONFLICT DO NOTHING;
INSERT INTO public.seniority VALUES(7,'senior'),(9,'intern') ON CONFLICT DO NOTHING;
INSERT INTO public.currency_rate(currency,to_eur) VALUES('CHF',1.05),('EUR',1) ON CONFLICT DO NOTHING;
INSERT INTO public.location VALUES(1,NULL,'country',NULL,ARRAY['de']),(2,1,'city',400000,ARRAY['de']) ON CONFLICT DO NOTHING;
INSERT INTO public.location_name VALUES(1,'en','Switzerland',true),(2,'en','Zurich',true),(2,'de','Zürich',true);
