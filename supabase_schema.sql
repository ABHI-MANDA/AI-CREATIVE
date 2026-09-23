-- Production baseline for AI Creative Studio.
-- The application currently uses creative_records as a compatibility layer.
-- These relational tables are the recommended next migration for multi-tenant SaaS.

create extension if not exists pgcrypto;

create table if not exists public.brands (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid,
  name text not null,
  brand_dna jsonb not null default '{}'::jsonb,
  guidelines jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.campaigns (
  id uuid primary key default gen_random_uuid(),
  brand_id uuid references public.brands(id) on delete cascade,
  name text not null,
  objective text,
  audience text,
  status text not null default 'draft',
  context jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.creative_assets (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid references public.campaigns(id) on delete cascade,
  asset_type text not null,
  storage_path text,
  public_url text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.generations (
  id uuid primary key default gen_random_uuid(),
  asset_id uuid references public.creative_assets(id) on delete cascade,
  provider text,
  model text,
  prompt text,
  parameters jsonb not null default '{}'::jsonb,
  duration_ms integer,
  estimated_cost numeric(12,6),
  status text not null default 'queued',
  error text,
  created_at timestamptz not null default now()
);

create table if not exists public.reviews (
  id uuid primary key default gen_random_uuid(),
  asset_id uuid references public.creative_assets(id) on delete cascade,
  approved boolean,
  rating integer,
  notes text,
  evaluation jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_campaigns_brand_id on public.campaigns(brand_id);
create index if not exists idx_assets_campaign_id on public.creative_assets(campaign_id);
create index if not exists idx_generations_asset_id on public.generations(asset_id);
create index if not exists idx_reviews_asset_id on public.reviews(asset_id);
