-- Ozon RFBS cloud schema v1. Apply with `supabase db push`.
-- Clients authenticate with Supabase Auth. Never store marketplace/API secrets here.

create extension if not exists pgcrypto;

create type public.workspace_role as enum ('owner', 'editor', 'viewer');

create table public.workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(trim(name)) between 1 and 100),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.workspace_members (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role public.workspace_role not null default 'viewer',
  created_at timestamptz not null default now(),
  primary key (workspace_id, user_id)
);

create table public.devices (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  device_key uuid not null,
  name text not null check (char_length(trim(name)) between 1 and 100),
  app_version text not null default '',
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  unique (workspace_id, device_key)
);

create table public.shops (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  local_id text not null,
  platform text not null check (platform in ('ozon', 'wildberries', 'mercadolibre')),
  name text not null,
  external_seller_id text not null default '',
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  revision bigint not null default 1 check (revision > 0),
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, platform, local_id)
);

comment on table public.shops is 'Non-secret shop identity only. API keys, tokens and proxy credentials stay local.';

create table public.products (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  platform text not null check (platform in ('ozon', 'wildberries', 'mercadolibre')),
  shop_id uuid references public.shops(id) on delete set null,
  offer_id text not null,
  title text not null default '',
  status text not null default '',
  source_url text not null default '',
  supplier_url text not null default '',
  external_product_id text not null default '',
  external_variant_id text not null default '',
  category_id text not null default '',
  type_id text not null default '',
  currency_code text not null default '',
  price numeric(18, 4),
  old_price numeric(18, 4),
  stock integer check (stock is null or stock >= 0),
  ledger jsonb not null default '{}'::jsonb check (jsonb_typeof(ledger) = 'object'),
  revision bigint not null default 1 check (revision > 0),
  source_device_id uuid references public.devices(id) on delete set null,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index products_active_identity
  on public.products (workspace_id, platform, coalesce(shop_id, '00000000-0000-0000-0000-000000000000'::uuid), offer_id)
  where deleted_at is null;
create index products_sync_cursor on public.products (workspace_id, updated_at, id);

create table public.listing_jobs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  local_id text not null,
  platform text not null check (platform in ('ozon', 'wildberries', 'mercadolibre')),
  shop_id uuid references public.shops(id) on delete set null,
  offer_id text not null default '',
  state text not null,
  checkpoint integer not null default 0 check (checkpoint >= 0),
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  last_error text not null default '',
  revision bigint not null default 1 check (revision > 0),
  source_device_id uuid references public.devices(id) on delete set null,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, platform, local_id)
);
create index listing_jobs_sync_cursor on public.listing_jobs (workspace_id, updated_at, id);

create table public.product_pool_items (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  source text not null default 'seerfar',
  source_key text not null,
  data jsonb not null default '{}'::jsonb check (jsonb_typeof(data) = 'object'),
  mapping jsonb not null default '{}'::jsonb check (jsonb_typeof(mapping) = 'object'),
  revision bigint not null default 1 check (revision > 0),
  source_device_id uuid references public.devices(id) on delete set null,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, source, source_key)
);
create index product_pool_sync_cursor on public.product_pool_items (workspace_id, updated_at, id);

create table public.mercadolibre_drafts (
  id uuid primary key,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  account_id text not null default '',
  seller_sku text not null default '',
  title text not null default '',
  draft jsonb not null check (jsonb_typeof(draft) = 'object'),
  revision bigint not null default 1 check (revision > 0),
  source_device_id uuid references public.devices(id) on delete set null,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, id)
);
create index mercadolibre_drafts_sync_cursor on public.mercadolibre_drafts (workspace_id, updated_at, id);

create table public.sync_conflicts (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  entity_type text not null,
  entity_id uuid not null,
  local_revision bigint not null,
  remote_revision bigint not null,
  local_payload jsonb not null,
  remote_payload jsonb not null,
  resolved_at timestamptz,
  resolved_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now()
);

create or replace function public.set_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger workspaces_set_updated_at before update on public.workspaces
for each row execute function public.set_updated_at();
create trigger shops_set_updated_at before update on public.shops
for each row execute function public.set_updated_at();
create trigger products_set_updated_at before update on public.products
for each row execute function public.set_updated_at();
create trigger listing_jobs_set_updated_at before update on public.listing_jobs
for each row execute function public.set_updated_at();
create trigger product_pool_set_updated_at before update on public.product_pool_items
for each row execute function public.set_updated_at();
create trigger mercadolibre_drafts_set_updated_at before update on public.mercadolibre_drafts
for each row execute function public.set_updated_at();

create or replace function public.is_workspace_member(target_workspace uuid)
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.workspace_members
    where workspace_id = target_workspace and user_id = auth.uid()
  );
$$;

create or replace function public.can_edit_workspace(target_workspace uuid)
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.workspace_members
    where workspace_id = target_workspace
      and user_id = auth.uid()
      and role in ('owner', 'editor')
  );
$$;

create or replace function public.is_workspace_owner(target_workspace uuid)
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.workspace_members
    where workspace_id = target_workspace
      and user_id = auth.uid()
      and role = 'owner'
  );
$$;

create or replace function public.create_workspace(workspace_name text)
returns uuid language plpgsql security definer set search_path = '' as $$
declare new_id uuid;
begin
  if auth.uid() is null then raise exception 'authentication required'; end if;
  insert into public.workspaces(name, created_by)
  values (workspace_name, auth.uid()) returning id into new_id;
  insert into public.workspace_members(workspace_id, user_id, role)
  values (new_id, auth.uid(), 'owner');
  return new_id;
end;
$$;

revoke all on function public.create_workspace(text) from public;
grant execute on function public.create_workspace(text) to authenticated;

alter table public.workspaces enable row level security;
alter table public.workspace_members enable row level security;
alter table public.devices enable row level security;
alter table public.shops enable row level security;
alter table public.products enable row level security;
alter table public.listing_jobs enable row level security;
alter table public.product_pool_items enable row level security;
alter table public.mercadolibre_drafts enable row level security;
alter table public.sync_conflicts enable row level security;

create policy workspaces_select on public.workspaces for select
  using (public.is_workspace_member(id));
create policy workspaces_update on public.workspaces for update
  using (public.can_edit_workspace(id)) with check (public.can_edit_workspace(id));
create policy members_select on public.workspace_members for select
  using (public.is_workspace_member(workspace_id));
create policy members_manage on public.workspace_members for all
  using (public.is_workspace_owner(workspace_id))
  with check (public.is_workspace_owner(workspace_id));

do $$
declare table_name text;
begin
  foreach table_name in array array['devices','shops','products','listing_jobs','product_pool_items','mercadolibre_drafts','sync_conflicts'] loop
    execute format('create policy %I on public.%I for select using (public.is_workspace_member(workspace_id))', table_name || '_select', table_name);
    execute format('create policy %I on public.%I for insert with check (public.can_edit_workspace(workspace_id))', table_name || '_insert', table_name);
    execute format('create policy %I on public.%I for update using (public.can_edit_workspace(workspace_id)) with check (public.can_edit_workspace(workspace_id))', table_name || '_update', table_name);
    execute format('create policy %I on public.%I for delete using (public.can_edit_workspace(workspace_id))', table_name || '_delete', table_name);
  end loop;
end $$;

grant usage on schema public to authenticated;
grant select, insert, update, delete on all tables in schema public to authenticated;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('product-images', 'product-images', false, 20971520, array['image/jpeg','image/png','image/webp'])
on conflict (id) do nothing;

create policy product_images_select on storage.objects for select to authenticated
using (
  bucket_id = 'product-images'
  and public.is_workspace_member(((storage.foldername(name))[1])::uuid)
);
create policy product_images_insert on storage.objects for insert to authenticated
with check (
  bucket_id = 'product-images'
  and public.can_edit_workspace(((storage.foldername(name))[1])::uuid)
);
create policy product_images_update on storage.objects for update to authenticated
using (
  bucket_id = 'product-images'
  and public.can_edit_workspace(((storage.foldername(name))[1])::uuid)
);
create policy product_images_delete on storage.objects for delete to authenticated
using (
  bucket_id = 'product-images'
  and public.can_edit_workspace(((storage.foldername(name))[1])::uuid)
);
