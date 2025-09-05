-- Tablas sugeridas para registro de alertas
create table if not exists public.alerts (
  id bigserial primary key,
  ts bigint not null,
  network text not null,
  pair_address text,
  symbol text,
  price_usd numeric,
  liquidity_usd numeric,
  score int,
  reasons text,
  link text
);

-- Índices útiles
create index if not exists alerts_ts_idx on public.alerts (ts desc);
create index if not exists alerts_network_idx on public.alerts (network);

-- Políticas RLS (si activas RLS en el proyecto)
alter table public.alerts enable row level security;
create policy "allow anon insert" on public.alerts
  for insert
  to anon
  with check (true);
create policy "allow anon read" on public.alerts
  for select
  to anon
  using (true);



