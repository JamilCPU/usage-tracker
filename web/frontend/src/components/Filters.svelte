<script lang="ts">
  import { onDestroy } from "svelte";
  import type { DeviceMeta } from "../lib/api";
  import { presets, type Filters } from "../lib/filters";
  import { hourLabel } from "../lib/format";

  let { filters, devices, onchange }: {
    filters: Filters;
    devices: DeviceMeta[];
    onchange: (f: Filters) => void;
  } = $props();

  // svelte-ignore state_referenced_locally
  let appText = $state(filters.app);
  // svelte-ignore state_referenced_locally
  let domainText = $state(filters.domain);
  let timer: ReturnType<typeof setTimeout> | undefined;

  onDestroy(() => clearTimeout(timer));

  const quick = presets(new Date());

  function update(patch: Partial<Filters>): void {
    onchange({ ...filters, ...patch });
  }

  function toggleDevice(name: string): void {
    const has = filters.devices.includes(name);
    update({ devices: has ? filters.devices.filter((d) => d !== name) : [...filters.devices, name] });
  }

  function typed(): void {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (appText !== filters.app || domainText !== filters.domain) {
        update({ app: appText.trim(), domain: domainText.trim() });
      }
    }, 300);
  }

  function setHour(which: "hourFrom" | "hourTo", value: number): void {
    if (which === "hourFrom") update({ hourFrom: value, hourTo: Math.max(value, filters.hourTo) });
    else update({ hourTo: value, hourFrom: Math.min(value, filters.hourFrom) });
  }

  function isPreset(p: { from: string; to: string }): boolean {
    return p.from === filters.from && p.to === filters.to;
  }
</script>

<section class="card filters" aria-label="Filters">
  <div class="row">
    <span class="label">Devices</span>
    <div class="chips">
      {#each devices as d (d.device)}
        <button
          type="button"
          aria-pressed={filters.devices.includes(d.device)}
          onclick={() => toggleDevice(d.device)}>{d.device}</button
        >
      {/each}
      {#if devices.length === 0}<span class="muted">No devices yet</span>{/if}
    </div>
    {#if filters.devices.length === 0 && devices.length > 0}
      <span class="muted hint">All devices</span>
    {/if}
  </div>

  <div class="row">
    <span class="label">Dates</span>
    <div class="chips">
      {#each quick as p (p.label)}
        <button type="button" aria-pressed={isPreset(p)} onclick={() => update({ from: p.from, to: p.to })}>
          {p.label}
        </button>
      {/each}
    </div>
    <div class="dates">
      <label>From <input type="date" value={filters.from} onchange={(e) => e.currentTarget.value ? update({ from: e.currentTarget.value }) : (e.currentTarget.value = filters.from)} /></label>
      <label>To (excl.) <input type="date" value={filters.to} onchange={(e) => e.currentTarget.value ? update({ to: e.currentTarget.value }) : (e.currentTarget.value = filters.to)} /></label>
    </div>
  </div>

  <div class="row">
    <span class="label">Hours {hourLabel(filters.hourFrom)} to {hourLabel(filters.hourTo)}</span>
    <div class="sliders">
      <input type="range" min="0" max="23" aria-label="From hour" value={filters.hourFrom}
        oninput={(e) => setHour("hourFrom", Number(e.currentTarget.value))} />
      <input type="range" min="0" max="23" aria-label="To hour" value={filters.hourTo}
        oninput={(e) => setHour("hourTo", Number(e.currentTarget.value))} />
    </div>
  </div>

  <div class="row texts">
    <label>App <input type="text" placeholder="exact name" bind:value={appText} oninput={typed} /></label>
    <label>Domain <input type="text" placeholder="exact domain" bind:value={domainText} oninput={typed} /></label>
  </div>
</section>

<style>
  .filters {
    display: grid;
    gap: 14px;
  }
  .row {
    min-width: 0;
    display: grid;
    gap: 6px;
  }
  .label {
    font-size: 13px;
    color: var(--muted);
  }
  .hint {
    font-size: 12px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .dates,
  .texts {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }
  label {
    min-width: 0;
    display: grid;
    gap: 4px;
    font-size: 13px;
    color: var(--muted);
  }
  .sliders {
    display: grid;
    gap: 0;
  }
</style>
