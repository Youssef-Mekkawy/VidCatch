# Font licenses

The `.woff2` files in this folder are subsets taken from the [Fontsource](https://fontsource.org)
packages. All four families are licensed under the **SIL Open Font License 1.1**
(<https://openfontlicense.org>), which allows bundling and redistribution.

| Family | Copyright holder | Files |
| --- | --- | --- |
| Space Grotesk | The Space Grotesk Project Authors (floriankarsten/space-grotesk) | `space-grotesk-*` |
| Inter | The Inter Project Authors (rsms/inter) | `inter-*` |
| IBM Plex Mono | IBM Corp. (IBM/plex) | `ibm-plex-mono-*` |
| IBM Plex Sans Arabic | IBM Corp. (IBM/plex) | `ibm-plex-sans-arabic-*` |

Only the Latin glyphs (and, for IBM Plex Sans Arabic, the Arabic glyphs) are included, to keep the
app small. To add another script, copy the matching subset file from the Fontsource package and
add an `@font-face` rule in `css/fonts.css`.
