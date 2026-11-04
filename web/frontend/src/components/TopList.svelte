<script lang="ts">
  import { fmtDuration } from "../lib/format";

  let { title, items }: { title: string; items: { name: string | null; seconds: number }[] } = $props();
  const max = $derived(Math.max(1, ...items.map((i) => i.seconds)));
</script>

<section class="card" aria-label={title}>
  <h2>{title}</h2>
  {#if items.length === 0}
    <p class="empty">Nothing recorded.</p>
  {:else}
    <ol>
      {#each items as it, i (i)}
        <li>
          <div class="line"><span class="name">{it.name ?? "(unknown)"}</span><span class="dur">{fmtDuration(it.seconds)}</span></div>
          <div class="track"><div class="fill" style="width:{(it.seconds / max) * 100}%"></div></div>
        </li>
      {/each}
    </ol>
  {/if}
</section>

<style>
  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 8px;
  }
  .line {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    font-size: 14px;
  }
  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .dur {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .track {
    height: 6px;
    border-radius: 3px;
    background: var(--surface-2);
    margin-top: 3px;
  }
  .fill {
    height: 100%;
    border-radius: 3px;
    background: var(--accent);
  }
</style>
