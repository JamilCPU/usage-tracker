<script lang="ts">
  import { onMount } from "svelte";
  import {
    getDaily, getHourly, getMeta, getTopApps, getTopDomains,
    type DailyRow, type DeviceMeta, type HourlyRow, type TopApp, type TopDomain,
  } from "./lib/api";
  import { defaultFilters, fromQuery, syncUrl, type Filters as FilterState } from "./lib/filters";
  import FiltersBar from "./components/Filters.svelte";
  import HourlyChart from "./components/HourlyChart.svelte";
  import TopList from "./components/TopList.svelte";
  import DailyTotals from "./components/DailyTotals.svelte";
  import DeviceCompare from "./components/DeviceCompare.svelte";
  import EventsTable from "./components/EventsTable.svelte";

  let filters = $state<FilterState>(fromQuery(location.search, defaultFilters(new Date())));
  let devices = $state<DeviceMeta[]>([]);
  let hourly = $state<HourlyRow[]>([]);
  let apps = $state<TopApp[]>([]);
  let domains = $state<TopDomain[]>([]);
  let daily = $state<DailyRow[]>([]);
  let loading = $state(true);
  let error = $state("");
  let token = 0;

  onMount(() => {
    getMeta()
      .then((m) => (devices = m.devices))
      .catch((e: unknown) => (error = e instanceof Error ? e.message : "Failed to load"));
  });

  async function refresh(f: FilterState): Promise<void> {
    const mine = ++token;
    loading = true;
    error = "";
    try {
      const [h, a, d, dl] = await Promise.all([getHourly(f), getTopApps(f), getTopDomains(f), getDaily(f)]);
      if (mine !== token) return;
      hourly = h.rows;
      apps = a.rows;
      domains = d.rows;
      daily = dl.rows;
    } catch (e: unknown) {
      if (mine === token) error = e instanceof Error ? e.message : "Failed to load";
    } finally {
      if (mine === token) loading = false;
    }
  }

  $effect(() => {
    const snapshot = $state.snapshot(filters);
    syncUrl(snapshot);
    void refresh(snapshot);
  });
</script>

<main>
  <header>
    <h1>Usage</h1>
    <a class="logout" href="/auth/logout">Log out</a>
  </header>

  <FiltersBar {filters} {devices} onchange={(f) => (filters = f)} />

  {#if error}<div class="banner" role="alert">{error}</div>{/if}
  {#if loading}<div class="loading" aria-live="polite">Loading...</div>{/if}

  <div class="grid" class:stale={loading}>
    <div class="wide"><DeviceCompare rows={daily} /></div>
    <div class="wide"><HourlyChart rows={hourly} hourFrom={filters.hourFrom} hourTo={filters.hourTo} /></div>
    <TopList title="Top apps" items={apps.map((a) => ({ name: a.app, seconds: a.seconds }))} />
    <TopList title="Top domains" items={domains.map((d) => ({ name: d.domain, seconds: d.seconds }))} />
    <div class="wide"><DailyTotals rows={daily} /></div>
    <div class="wide"><EventsTable {filters} /></div>
  </div>
</main>

<style>
  main {
    max-width: 960px;
    margin: 0 auto;
    padding: 12px;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  h1 {
    margin: 0;
    font-size: 22px;
  }
  .logout {
    min-height: 40px;
    display: inline-flex;
    align-items: center;
  }
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    align-items: start;
    transition: opacity 0.15s;
  }
  .grid.stale {
    opacity: 0.6;
  }
  .grid > :global(*) {
    min-width: 0;
  }
  .wide {
    grid-column: 1 / -1;
  }
  .banner {
    background: var(--danger);
    color: #fff;
    padding: 10px 12px;
    border-radius: 8px;
  }
  .loading {
    color: var(--muted);
    font-size: 13px;
  }
  @media (max-width: 640px) {
    .grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
