# Marketing artwork added September 2026

These are newly sourced works, rather than repeats of the homepage artwork.
Original downloads are retained in `master/`. The PNGs remove the paper and
retain translucent ink, using `script/stylize_images.py`. `_dark` means black
ink for light surfaces; `_light` means white ink for dark surfaces.

| File stem | Work | Source and rights |
| --- | --- | --- |
| `the_ploughman` | Hans Holbein, *The Ploughman*, 1523–5 | [Public Domain Image Archive / Rijksmuseum](https://pdimagearchive.org/images/ff74e7ae-cc8f-468c-b77e-43c119df5290/): public domain worldwide, digitization marked CC0. |
| `the_emperor` | Hans Holbein, *The Emperor*, 1523–5 | [Public Domain Image Archive / Rijksmuseum](https://pdimagearchive.org/images/ab0eeb6f-a1af-4764-8624-0caf624551e2/): public domain worldwide, digitization marked CC0. |

Original download URLs:

- https://images.pdimagearchive.org/collections/hans-holbeins-dance-of-death-1523-5/40606008735_db80f8fe88_o.jpg
- https://images.pdimagearchive.org/collections/hans-holbeins-dance-of-death-1523-5/40606050395_3a1dceb6f4_o.jpg

Regenerate with `python script/stylize_images.py` from `apps/web` (requires
Pillow). The original JPEGs are unmodified. Display crops live in
`src/content/config.ts`: the tracker crop preserves the central figures and removes the paper border.
The existing `PublicDomainArt` component provides theme inversion, image
optimization, and the same overlay source credits used across the site.


## Narrowed illustration

`the_woodcutter`: Jost Amman, *A craftsman making a woodcut* ("Der
Formschneider"), 1568. [Wellcome Collection, 34981i](https://wellcomecollection.org/works/sdq6u6kq),
Public Domain Mark. [Source image](https://iiif.wellcomecollection.org/image/V0040551ETC/full/full/0/default.jpg).
The original is kept under `master/`; both transparent line variants use
`script/stylize_images.py` with the existing threshold of 150. Display cropping
is configured in `publicDomainAssets`; the existing artwork component supplies
the overlay credit and dark-theme inversion.
