import { Actor, Dataset, KeyValueStore } from 'apify';

export async function openWritableDataset(preferredName?: string): Promise<Dataset> {
  if (preferredName) {
    try {
      return await Actor.openDataset(preferredName);
    } catch (err) {
      console.warn(`Falling back to default dataset because '${preferredName}' is unavailable:`, err);
    }
  }

  return Actor.openDataset();
}

export async function pushDataWithFallback(data: unknown[], preferredName?: string): Promise<void> {
  const defaultDataset = await Actor.openDataset();
  for (const item of data) {
    await defaultDataset.pushData(item);
  }

  if (!preferredName) return;

  try {
    const namedDataset = await Actor.openDataset(preferredName);
    if (namedDataset.id !== defaultDataset.id) {
      for (const item of data) {
        await namedDataset.pushData(item);
      }
    }
  } catch (err) {
    console.warn(`Skipped writing to named dataset '${preferredName}':`, err);
  }
}

export async function openKeyValueStoreWithFallback(preferredName?: string): Promise<KeyValueStore> {
  if (preferredName) {
    try {
      return await Actor.openKeyValueStore(preferredName);
    } catch (err) {
      console.warn(`Falling back to default key-value store because '${preferredName}' is unavailable:`, err);
    }
  }

  return Actor.openKeyValueStore();
}
