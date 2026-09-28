# Marketing artwork added September 2026

These are newly sourced works, rather than repeats of the homepage artwork.
Original downloads are retained in `master/`. The PNGs remove the paper and
retain translucent ink, using `script/stylize_images.py`. `_dark` means black
ink for light surfaces; `_light` means white ink for dark surfaces.

| File stem | Work | Source and rights |
| --- | --- | --- |
| `the_ploughman` | Hans Holbein, *The Ploughman*, 1523–5 | [Public Domain Image Archive / Rijksmuseum](https://pdimagearchive.org/images/ff74e7ae-cc8f-468c-b77e-43c119df5290/): public domain worldwide, digitization marked CC0. |
| `poliphilus_writing` | *Poliphilus alone, writing to his beloved Polia*, from *Hypnerotomachia Poliphili*, 1499 | [Public Domain Image Archive / McGill University Library](https://pdimagearchive.org/images/f891503a-3ce5-43f3-a653-8d7fb78f631c/): public domain worldwide, no additional digital rights. |

Original download URLs:

- https://images.pdimagearchive.org/collections/hans-holbeins-dance-of-death-1523-5/40606008735_db80f8fe88_o.jpg
- https://images.pdimagearchive.org/essays/hypnerotomachia-poliphili/17-rbsc_hypnerotomachia-poliphili_foliolncun1499Colonna_0448-edit.jpeg

Regenerate with `python script/stylize_images.py` from `apps/web` (requires
Pillow). The original JPEGs are unmodified. Display crops live in
`src/content/config.ts`: the tracker crop focuses on the writer and desk.
The existing `PublicDomainArt` component provides theme inversion, image
optimization, and the same overlay source credits used across the site.
