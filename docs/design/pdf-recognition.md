# How Zotero gets metadata for a PDF

Design research for apr. Read for understanding; nothing here may be copied into
apr (Zotero is AGPL-3.0, apr is MIT). All prose below is my own. The only
verbatim third-party fragments are three regexes and a handful of identifiers
that would lose their meaning if paraphrased.

**Revisions inspected — accessed 2026-10-15 (UTC).**

| Ref | Revision |
| --- | --- |
| `zotero/zotero` `main` | `db41b99fddcc52d38897c59c161b6adaca036afb` |
| `zotero/zotero` (historical) | `ce0dd5cc5e8d38fcdbc8bf6174eb4101c3185426` (parent of `991a50d0` "New PDF recognizer", 2018-01-20) |
| `zotero/document-worker` `master` | `a8bab24c82a41435f125a17a4865951b74518117` |
| `zotero/translate` `master` | `dd524aea9a556cd23eb9dc8cc617219a98d0fb47` |
| `zotero/translators` `master` | `0583a9e1ed4fd9f66da2bfc42ccfb524c1d944b0` |

Citations are by repository, file path and symbol, with a pinned raw URL to the
revision I read. I read raw blobs, which carry no line numbers, so I cite
symbols and URLs rather than inventing line numbers.

Evidence labels used below: **[direct]** = read in source or official docs,
**[interp]** = my reading of that source, **[infer]** = my reasoning beyond it.

---

## 1. Stages of Zotero's PDF metadata retrieval

**[direct]** The feature is `Zotero.RecognizeDocument`
(`chrome/content/zotero/xpcom/recognizeDocument.js`, previously `recognizePDF.js`
and, before 2018, `chrome/content/zotero/recognizePDF.js`). Per attachment:

1. **Queue and gate.** `recognizeItems()` enqueues attachments that pass
   `canRecognize()`: the item must have an attachment content type, be a PDF or
   EPUB attachment, and be a top-level item (no parent). `autoRecognizeItems()`
   only runs when the pref `autoRecognizeFiles` is set; the default shipped in
   `defaults/preferences/zotero.js` is `true`. The queue waits out
   `Zotero.HTTP.browserIsOffline()` in 60-second loops (`OFFLINE_RECHECK_DELAY`).
2. **Local extraction.** `_recognize()` dispatches on attachment type to
   `_recognizePDF()` or `_recognizeEPUB()`. For PDFs, `extractPDFJSON()` calls
   `Zotero.PDFWorker.getRecognizerData(itemID, true)` — i.e. the PDF worker
   (`document-worker`) reads the file and returns a JSON document. Failure here
   raises the alert `recognizePDF.couldNotRead`
   ("Could not read text from document").
3. **Client-side text sanity check.** `_recognizePDF()` counts pages whose
   `page[2]` is non-empty (`containingTextPages`); if none, it throws
   `recognizePDF.noOCR` ("PDF does not contain OCRed text") and never contacts
   the network.
4. **Remote recognition.** The JSON is sent to the recognizer service (section 2)
   and the response is a *candidate description*, not an item: the fields the
   client reads are `arxiv`, `doi`, `isbn`, `type`, `title`, `authors[]`,
   `abstract`, `year`, `pages`, `volume`, `url`, `language`, `issue`, `ISSN`,
   `issn`, `container`, `publisher`.
5. **Resolution, in a fixed precedence order** (`_recognizePDF()`): by arXiv ID,
   then DOI, then ISBN search, then finally a bare title/authors record.
6. **Item construction and attachment rewiring.** The resolved (or synthesised)
   item is saved; the attachment is moved into the parent's collections and made
   a child of the new item; the file is renamed when
   `Zotero.Attachments.shouldAutoRenameAttachment()` allows it; the attachment
   title is replaced with the file-type label via `setAutoAttachmentTitle()`.
   `_newItems` remembers the original title/filename plus `dateModified` so
   `canUnrecognize()`/`unrecognize()` can undo the operation for up to one day
   (`UNRECOGNIZE_TIMEOUT = 86400 * 1000`), provided the item was not modified
   (notes within a 1000 ms `NOTE_EDIT_THRESHOLD`) and has exactly one
   attachment.
7. **Reporting that the match was wrong.** `report(item, description)` POSTs
   `{description, version, json, metadata}` to `<base>/report` — the extracted
   JSON *and* the saved item JSON. **[interp]** This is a manual feedback path.

Source URL for step 1-7 (symbol: `Zotero.RecognizeDocument`):
https://raw.githubusercontent.com/zotero/zotero/db41b99fddcc52d38897c59c161b6adaca036afb/chrome/content/zotero/xpcom/recognizeDocument.js

User-visible strings: https://raw.githubusercontent.com/zotero/zotero/db41b99fddcc52d38897c59c161b6adaca036afb/chrome/locale/en-US/zotero/zotero.properties
(`recognizePDF.title`, `.noOCR`, `.couldNotRead`, `.noMatches`, `.fileNotFound`).

**Historical note [direct].** Before the 2018 rewrite the whole thing was local:
`Zotero_RecognizePDF.recognize()` ran a PDF text extractor
(`Zotero.Fulltext.getPDFConverterExecAndArgs()`, `-nopgbrk -layout -l 15`), looked
for a DOI in the first 80 lines, then an ISBN, then fell back to scraping Google
Scholar and translating the result page. The DOI → ISBN → full-text-search
ordering therefore predates the service; the service replaced only the last step.

Historical file:
https://raw.githubusercontent.com/zotero/zotero/ce0dd5cc5e8d38fcdbc8bf6174eb4101c3185426/chrome/content/zotero/recognizePDF.js

## 2. How much of the PDF is read, and what is sent where

**How much [direct]:** at most the **first five pages**, page indices 0–4. In
`document-worker`'s `getRecognizerData()` the cap is the literal
`let maxPages = 5;`, clamped to the page count; there is no preference for it.
Note the client module also declares `const MAX_PAGES = 5;` (and
`EPUB_MAX_SECTIONS = 5` for EPUB sections), so the number is duplicated in two
places — a hard ceiling, not a tuning knob. The *total* page count is sent as
`totalPages` even though the text stops at page 5.

Worker source (symbol `getRecognizerData`):
https://raw.githubusercontent.com/zotero/document-worker/a8bab24c82a41435f125a17a4865951b74518117/src/pdf/index.js
Worker is invoked as op `pdf.getRecognizerData` with `{buf, password}`:
https://raw.githubusercontent.com/zotero/zotero/db41b99fddcc52d38897c59c161b6adaca036afb/chrome/content/zotero/xpcom/pdfWorker/manager.js

**What is extracted [direct].** Not plain text — a layout tree:

- `metadata`: every string-valued key of the PDF's `documentInfo` except
  `PDFFormatVersion`, plus string-valued entries of the nested `Custom`
  dictionary. This is the PDF's *embedded* metadata (Title, Author, Subject, …).
- `totalPages`: integer.
- `pages`: one entry per page read, shaped
  `[pageWidth, pageHeight, [[[[0, 0, 0, 0, lines]]]]]` where `lines` is a list
  of lines, each line a list of word records. A word record is a 14-element
  array: `[xMin, yMin, xMax, yMax, fontSize, spaceAfter, baseline, rotation,
  underlined, bold, italic, colorIndex, fontIndex, text]`, coordinates rounded to
  four decimals and `y` measured up from the page bottom; `fontIndex` indexes a
  per-page font-name list that is **not** included in the output, and `rotation`,
  `underlined` and `colorIndex` are hard-coded to 0. **[interp]** The payload
  reserves layout signal (font, size, weight, position, predicted
  underline/hyperlink) that the recognizer service may use for section/heading
  detection.

**The client then adds `json.fileName = <basename of the attachment file>` before
sending [direct]** (`_recognizePDF()`), so the request body is
`{metadata, totalPages, pages, fileName}`.

**Where it goes [direct].** `_getBaseURL()` resolves, in order: the hidden pref
`recognize.url` if set (used verbatim, trailing slash added); otherwise the pref
`services.url` or `ZOTERO_CONFIG.SERVICES_URL`, with the suffix `recognizer/`
appended. The shipped default is `SERVICES_URL: 'https://services.zotero.org/'`
in `resource/config.mjs`, so the production endpoint is
**`https://services.zotero.org/recognizer/recognize`** (POST). The request is
made through `Zotero.Sync.Runner.getAPIClient().makeRequest(...)` with
`noAPIKey: true`, `Content-Type: application/json`, `successCodes: [200]`, and
the response is parsed as JSON.

- Base URL: https://raw.githubusercontent.com/zotero/zotero/db41b99fddcc52d38897c59c161b6adaca036afb/chrome/content/zotero/xpcom/recognizeDocument.js
- `SERVICES_URL`: https://raw.githubusercontent.com/zotero/zotero/db41b99fddcc52d38897c59c161b6adaca036afb/resource/config.mjs
- `noAPIKey` / header behaviour (`getHeaders`, `makeRequest`):
  https://raw.githubusercontent.com/zotero/zotero/db41b99fddcc52d38897c59c161b6adaca036afb/chrome/content/zotero/xpcom/sync/syncAPIClient.js

**Privacy implications [direct + infer].**

- The **full extracted structure** — five pages of word-level text with
  coordinates, plus the PDF's embedded metadata and the local **file name** — is
  sent to a Zotero-operated server. It is not an anonymous hash or an identifier
  list; the document *content* leaves the machine.
- `noAPIKey: true` means the request carries no Zotero credentials. The API
  client only adds a `Zotero-API-Key` header when it holds a key, and `makeRequest`
  additionally permits a keyless call when `noAPIKey` is set. **[infer]** So the
  request cannot be linked to a Zotero account by the client's own headers; the
  server still sees the source IP, and `getHeaders()` always adds
  `Zotero-API-Version` and `Zotero-Schema-Version`.
- Official docs: "[t]he Zotero client sends the first few pages of text from the
  PDF to the web service, which uses a variety of extraction algorithms and known
  metadata from Crossref, paired with DOI and ISBN lookups"; "[t]he Zotero lookup
  service doesn't require a Zotero account and doesn't log any data about the
  content or results of searches."
  https://www.zotero.org/support/retrieve_pdf_metadata
- The announcement of the service says the same and adds that the old Google
  Scholar path is gone:
  https://forums.zotero.org/discussion/70567/available-for-beta-testing-new-pdf-recognizer
- The privacy policy lists "Automatic PDF metadata retrieval" as an opt-out
  (General preferences), states "[w]e do not log any information about the
  contents of PDF metadata requests", and separately states that server requests
  are logged with IP address and client information for up to 90 days, and that
  Crossref/Library of Congress lookups may log the IP address and the search terms
  (DOI/ISBN).
  https://www.zotero.org/support/privacy
- **[direct]** The recognizer server itself is **not open source**: "That service
  depends on internal server-side parts that are difficult to expose, and it
  involves logic on both the client and the server" / "Some of the recognizer code
  used to be open, but it wasn't usable externally because of it's interaction
  with private Zotero services, and so, to avoid confusion, devs made that repo
  private."
  https://forums.zotero.org/discussion/84594/source-for-services-zotero-org-recognizer-recognize
  I found no public mirror (GitHub repository search for a Zotero recognizer
  repository returns only an unrelated third-party plugin).

**Encrypted PDFs [direct].** `extractPDFJSON()` calls
`getRecognizerData(itemID, true)`; the manager signature is
`getRecognizerData(itemID, isPriority, password)`, so no password is ever passed
by the recognition path. **[interp]** A password-protected PDF therefore fails in
the worker and surfaces as `recognizePDF.couldNotRead`.

## 3. How identifiers are detected

**PDF path: server-side, opaque [direct/infer].** The client extracts no
identifiers from a PDF at all. `_recognizePDF()` only reads `res.arxiv`,
`res.doi`, `res.isbn` from the service response. Official docs attribute
detection to the service: "a variety of extraction algorithms and known metadata
from Crossref, paired with DOI and ISBN lookups". Good PDFs may also simply come
back with a title/author record and no identifier. **[infer]** The word-geometry
payload exists precisely because the service does this detection server-side
(likely using layout position, font size and weight to separate the title block,
author list and footer/DOI line). No public code confirms how.

**EPUB path: client-side [direct].** `_recognizeEPUB()` is fully local:

- ISBN from the EPUB's RDF metadata, split on spaces, each value passed through
  `Zotero.Utilities.cleanISBN`, survivors re-joined with spaces.
- Then it walks the `copyright-page` document if present, plus the first five
  section documents (`EPUB_MAX_SECTIONS = 5`), and stops early once it has both a
  DOI and an ISBN.
- DOI detection is a copy of the DOI translator's rule (the comment in source says
  "Copied from DOI translator"); the same regex appears in
  `zotero/translators` `DOI.js`. Trailing `)` / `}` are stripped only when the
  matching bracket is absent, and a trailing `/pdf...` segment is stripped unless
  the DOI already contains an `http(s)://` URL (the `translators` copy has the
  `/pdf` rule; the `recognizeDocument.js` copy does not). The regex, quoted
  because its shape matters, is:
  `/\b10\.[0-9]{4,}\/[^\s&"']*[^\s&"'.,]/g`
- ISBN detection from EPUB text is just `Zotero.Utilities.cleanISBN(<body text>)`.
- There is **no arXiv detection and no PMID detection** in this path.

https://raw.githubusercontent.com/zotero/zotero/db41b99fddcc52d38897c59c161b6adaca036afb/chrome/content/zotero/xpcom/recognizeDocument.js
https://raw.githubusercontent.com/zotero/translators/0583a9e1ed4fd9f66da2bfc42ccfb524c1d944b0/DOI.js

**PMID [direct].** `Zotero.Translate.Search.prototype.setIdentifier()` accepts
`DOI`, `ISBN`, `PMID`, `arXiv` and `adsBibcode` (PMID becomes a `contextObject`
string `info:pmid/<pmid>`), but `_recognizePDF()`/`_recognizeEPUB()` never pass
PMID and no `res.pmid` branch exists. **[infer]** A PMID returned by the service
is ignored by the current client.

## 4. Which APIs are queried, and the exact query shapes

### 4.1 The recognizer service (always, for PDFs)

`POST https://services.zotero.org/recognizer/recognize` (or the configured
`recognize.url` / `services.url` + `recognizer/`), `Content-Type:
application/json`, body = the extracted JSON described in section 2, no API key,
expects `200` with a JSON body. A related feedback endpoint is `POST <base>/report`.

### 4.2 DOI resolution (client-side, after the service returns a DOI)

**[direct]** `translate.setIdentifier({DOI: res.doi})` builds the search
`{itemType: 'journalArticle', DOI: <doi>}`; `getTranslators()` collects matching
translators and `setTranslator()` fixes the list.

`Zotero.Translate.Search` semantics (from `zotero/translate`): `getTranslators()`
returns all translators of type `search`; `_detectTranslatorsCollected()` sorts
them by `priority` **ascending** (ties broken by `runMode`); `complete()` shifts
to the next translator whenever the current one yields no items, and reports
`ERROR_NO_RESULTS = "No items returned from any translator"` when the list is
exhausted.

- https://raw.githubusercontent.com/zotero/translate/dd524aea9a556cd23eb9dc8cc617219a98d0fb47/src/translation/translate.js

**[direct]** The translator that resolves DOIs in practice is **DOI Content
Negotiation** (`translatorID b28d0d42-8549-4c6d-83fc-8382874a5cb9`, priority 100,
`translatorType: 8` = search). `detectSearch` returns true when the input carries a
clean DOI. For each DOI it performs:

- `GET https://doi.org/<url-encoded DOI>` with
  `Accept: application/vnd.datacite.datacite+json, application/vnd.crossref.unixref+xml, application/vnd.citationstyles.csl+json`
- response sniffing: contains `<crossref` → Crossref Unixref importer
  (`93514073-b541-4e02-9180-c36d2f3bb401`), `libraryCatalog = DOI.org (Crossref)`;
  contains `http://datacite.org/schema` or `"agency": "DataCite"` or
  `"providerId":` → Datacite JSON importer (`b5b5808b-1c61-473d-9a02-e1f5ba7b8eef`),
  `libraryCatalog = DOI.org (Datacite)`; otherwise CSL JSON importer
  (`bc03b4fe-436d-4a1f-ba59-de4d2d7a63f7`), `libraryCatalog = DOI.org (CSL JSON)`.
- A registration-agency probe `GET https://doi.org/ra/<doi>` exists in the file but
  is disabled (`if (false) { ... }`), with a comment that Crossref REST is used
  instead for reliability.

https://raw.githubusercontent.com/zotero/translators/0583a9e1ed4fd9f66da2bfc42ccfb524c1d944b0/DOI%20Content%20Negotiation.js

**[direct]** A second DOI-capable search translator exists: **Crossref REST**
(`0a61e167-de9a-4f93-a68a-628b48855909`, priority 90), which queries
`https://api.crossref.org/works/<query>` where `<query>` is either
`?filter=doi:<cleanDOI>` (or `?filter=doi:<a>,doi:<b>` for a DOI list) or
`?query.bibliographic=<url-encoded query>`, appending `&mailto=<email>` when the
hidden pref `CrossrefREST.email` is set; it sets `libraryCatalog = Crossref`.
Its own `detectSearch` returns `false`, so it is only reachable through the DOI
translator's explicit hand-off (which is itself disabled) or another translator.

https://raw.githubusercontent.com/zotero/translators/0583a9e1ed4fd9f66da2bfc42ccfb524c1d944b0/Crossref%20REST.js

### 4.3 arXiv resolution

**[direct]** `translate.setIdentifier({arXiv: res.arxiv})` →
`{itemType: 'journalArticle', arXiv: <id>}`. The **arXiv.org** translator's
`detectSearch` is `!!item.arXiv`, and `doSearch` calls:

- `GET https://export.arxiv.org/api/query?id_list=<url-encoded id>&max_results=1`
  (Atom XML). The parsed item gets `libraryCatalog = arXiv.org`, `archiveID =
  "arXiv:<id>"`, `extra = "arXiv:<id> [<primary category>]"`, and a DOI of the form
  `10.48550/arXiv.<id>` when the entry has no published DOI; a published DOI, when
  present, is resolved by recursively invoking the DOI translator.

https://raw.githubusercontent.com/zotero/translators/0583a9e1ed4fd9f66da2bfc42ccfb524c1d944b0/arXiv.org.js

### 4.4 ISBN search

**[direct]** `translate.setSearch({itemType: 'book', ISBN: res.isbn})` runs every
search translator whose `detectSearch` accepts an ISBN, in ascending priority
order, and takes the first that returns items. Verified ISBN-capable search
translators in `zotero/translators`:

| Translator | ID | priority | type | Query |
| --- | --- | --- | --- | --- |
| Open WorldCat | `c73a4a8c-3ef1-4ec8-8229-7531ee384cc4` | 100 | 12 (web+search) | `doSearch` accepts `item.ISBN` (and `identifiers.oclc`); scrapes worldcat.org |
| K10plus ISBN | `de0eef58-cb39-4410-ada0-6b39f43383f9` | 99 | 8 | `GET https://sru.k10plus.de/opac-de-627?version=1.1&operation=searchRetrieve&query=pica.isb=<cleanISBN>&maximumRecords=1` (MARCXML, imported by `edd87d07-9194-42f8-b2ad-997c4c7deefd`) |
| Library of Congress ISBN | `c070e5a2-4bfd-44bb-9b3c-4be20c50d0d9` | 97 | 8 | `GET https://lx2.loc.gov/sru/lcdb?operation=searchRetrieve&version=1.1&query=bath.ISBN=^<cleanISBN>&maximumRecords=1` (same MARCXML importer) |

K10plus additionally prepends the queried ISBN to the item's ISBN list and clears
German-specific fields; both SRU translators note in comments they use SRU/CQL
against the library catalogue.

- https://raw.githubusercontent.com/zotero/translators/0583a9e1ed4fd9f66da2bfc42ccfb524c1d944b0/K10plus%20ISBN.js
- https://raw.githubusercontent.com/zotero/translators/0583a9e1ed4fd9f66da2bfc42ccfb524c1d944b0/Library%20of%20Congress%20ISBN.js
- https://raw.githubusercontent.com/zotero/translators/0583a9e1ed4fd9f66da2bfc42ccfb524c1d944b0/Open%20WorldCat.js

**[interp]** Because the sort is ascending by priority, a clean run should try
Library of Congress (97) before K10plus (99) before Open WorldCat (100). I did not
observe a live run, so treat the *runtime* winner as unverified; the ordering
comparator itself is direct evidence.

### 4.5 The service's own lookups

**[direct]** The service uses "extraction algorithms and known metadata from
CrossRef, paired with DOI and ISBN lookups" (docs and announcement, URL above).
Its endpoints, scoring and thresholds are **not public** (section 2 and 6).

## 5. Candidate matching and scoring

**[direct]** I could not read the matching algorithm: it is server-side, and the
service repository is private (section 2). What the open client reveals:

- **The decision hierarchy is the client's, and it is ordered**: arXiv → DOI →
  ISBN → synthesised record from `res.title` + `res.authors`. Each earlier branch
  that succeeds returns immediately; a later branch is only reached when the
  earlier ones returned nothing (no `res.arxiv`/`res.doi`/`res.isbn`, or the
  translator attempt threw).
- **Fields consumed from the service**: for the synthesised-record branch, `type`
  (`'book-chapter'` → `bookSection`, anything else → `journalArticle`), `title`,
  `authors[].firstName/lastName` (all mapped to creator type `author`),
  `abstract` → `abstractNote`, `year` → `date`, `pages`, `volume`, `url`,
  `language` → `language`, `issue`, `container` → `publicationTitle`
  (journalArticle) or `bookTitle` (bookSection), `publisher` (bookSection only),
  and ISBN-derived `issn` from `res.issn` guarded by `if (res.ISSN)`. The item is
  stamped `libraryCatalog = 'Zotero'`.
- **A source-level inconsistency worth knowing about [direct]:** the ISSN guard
  tests `res.ISSN` (upper case) while the value assigned is `res.issn` (lower
  case). **[interp]** As written, an ISSN only lands in the item if the service
  sends *both* spellings.
- **No confidence value is read.** The client never looks at a score, threshold or
  probability in the response, and never re-verifies a returned DOI/arXiv ID
  against the PDF's own text.
- **Selection lists are resolved greedily**: the `select` handler installed by
  `_promiseTranslate()` calls back with `{ [firstKey]: firstValue }` and returns,
  i.e. the first candidate in any translator-provided selection list wins.
- **Tags**: ISBN-branch items get automatic-type tags only when the pref
  `automaticTags` is on, otherwise `tags = []`. The other branches rely on the
  translator/importer behaviour.

**Historical algorithm, with its real thresholds [direct]** (pre-2018
`recognizePDF.js`, useful as a sanity model for apr's own matcher, not as a
specification of today's service):

1. Extract up to 15 pages of text (`-nopgbrk -layout -l 15`), split into non-empty
   trimmed lines.
2. Look for a DOI in the first **80 lines** only ("to avoid catching article
   references"); a JSTOR stable URL is converted to `10.2307/<id>`.
3. Otherwise look for ISBNs using a pattern that requires a literal `SBN`/`ISBN`
   token, tolerating spaces and en/em dashes; because publishers print two ISBNs
   side by side, lengths 20/26 are split in half and length 23 is split 10+13;
   every candidate is validated with `cleanISBN`.
4. Otherwise Google Scholar full-text search: keep at most 100 lines that have
   more than three words and come from a single column
   (`/^[\s_]*([^\s]+(?: [^\s_]+)+)/`); **abort with `recognizePDF.noOCR` if fewer
   than 20 candidate lines survive**, or if the first line is Google's scanned-book
   boilerplate ("This is a digital copy of a book that was preserved …").
5. Compute the **median** candidate line length and keep only lines within ±6
   characters of it ("this is completely arbitrary").
6. Build the query from quoted phrase fragments: each picked line loses its first
   and last word, lines containing a word longer than 20 characters are skipped,
   and lines are taken with a stride of 7 (`nextLine = (nextLine + 7) %
   goodLines.length`) to avoid adjacent sentences; accumulate until **25 words**.
7. `GET https://scholar.google.com/scholar?q=<query>&hl=en&lr=&btnG=Search`, with a
   2000 ms delay between queries, up to 3 retries, CAPTCHA detection, and (after
   removing the `GDSESS`/`PREF` cookies) a CAPTCHA dialog for the user; persistent
   blocks raised `recognizePDF.limit`.

https://raw.githubusercontent.com/zotero/zotero/ce0dd5cc5e8d38fcdbc8bf6174eb4101c3185426/chrome/content/zotero/recognizePDF.js

## 6. How a retrieved record competes with values already in the PDF

**[direct]** For PDFs the client runs **no comparison**: `_recognizePDF()` never
reads the PDF's embedded metadata back, and never checks a retrieved DOI/title
against it. Competition happens only in one direction and only to fill gaps: in
all three identifier branches, `res.abstract` and `res.language` are written into
the new item **only if the retrieved record lacks them**
(`if (!newItem.abstractNote && res.abstract)` / `if (!newItem.language &&
res.language)`).

**[direct]** For EPUBs, embedded metadata is a *fallback with a guard*:

- Extract the ISBN from the EPUB RDF, then search by ISBN/DOI.
- If the returned item's ISBN set (after `cleanISBN` on each space-separated
  value) does **not** contain the extracted ISBN, the search result is discarded
  and the item is built from the EPUB's RDF metadata instead.
- If the identifier search throws, or no identifier was found, or the search
  produced no item, the RDF metadata is used.
- If there is neither a search result nor RDF metadata, the alert
  `recognizePDF.couldNotRead` is raised.

Covered by tests: "should use metadata from EPUB when search returns item with
different ISBN" and "should use metadata from EPUB when search fails", plus the
PDF test "should recognize a PDF by DOI and rename the file" which asserts
`libraryCatalog == "DOI.org (Crossref)"` (confirming the DOI Content Negotiation
path) and the arXiv test asserting the title from the arXiv record.
https://raw.githubusercontent.com/zotero/zotero/db41b99fddcc52d38897c59c161b6adaca036afb/test/tests/recognizeDocumentTest.js

**[infer]** So the honest summary for apr: the *client* prefers the retrieved
record, and treats the PDF's embedded metadata as a payload for the matcher rather
than a competing source; the *service* may well use embedded metadata to help
matching, but that is not observable.

## 7. What happens when nothing matches, or there is no text layer

| Situation | Client behaviour | Evidence |
| --- | --- | --- |
| No page among the first five has a non-empty text structure | Alert `recognizePDF.noOCR` ("PDF does not contain OCRed text"); no request sent | `_recognizePDF()` (`containingTextPages`) |
| Worker cannot read the file | Alert `recognizePDF.couldNotRead` ("Could not read text from document") | `extractPDFJSON()` |
| Service responds with nothing usable | `_recognize` returns null → alert `recognizePDF.noMatches` ("No matching references found"); progress row marked failed | `_processItem()` |
| Service returns an identifier but the lookup yields no item | The branch is caught and logged, then the next branch runs; if all fail, `recognizePDF.noMatches` | `_recognizePDF()`, `_promiseTranslate()` (`'No items found'`) |
| No DOI translator matches/available | Debug message only, then ISBN/title branches | `_recognizePDF()` (`"No translators found"`) |
| Browser offline | Queue sleeps 60 s and retries rather than failing | `_processQueue()` |
| A service request is in flight for a bad DOI | Historically fixed by not hanging; see commit `bd7cdbdc81a8dc26e6ebc638b81d263e674e68f2` "Don't hang on invalid DOI returned from recognizer-server" | commit history of the file |

**Uncertainty I could not resolve [direct observation + interp].** The client's
text check is `if (page[2].length) containingTextPages++`. With the current worker
output each page is `[pageWidth, pageHeight, [[[[0, 0, 0, 0, lines]]]]]`, whose
third element is a one-element array *regardless of whether `lines` is empty*, so
on paper the check would pass even for a scanned PDF. I could not find a code path
in `document-worker` or the client that prunes the empty structure, and the
"no OCR" string and user reports imply the alert does fire in practice. Treat the
exact predicate as **unverified**; the *observable* rule to design against is
"a text layer is required on at least one of the first five pages".

## 8. The three questions apr's behaviour depends on

**(1) Does Zotero ever prefer embedded PDF metadata over a retrieved record, and
under what rule?**

No — not for PDFs, and not under any threshold. `_recognizePDF()` contains no
comparison between the PDF's `documentInfo` and the retrieved record; embedded
metadata is only forwarded to the service, and the retrieved record wins outright.
The only merges are service → item gap-fills (`abstractNote`, `language`). The one
place where document-embedded metadata beats a retrieved record is the **EPUB**
path, under two explicit rules: (a) ISBN mismatch — the search result's cleaned
ISBN list does not contain the document's extracted ISBN → discard the result and
use the embedded RDF metadata; (b) search failure or absent identifiers/no result →
use the embedded RDF metadata. Verified by source and by the two EPUB tests named
in section 6. Whether the *service* prefers PDF-embedded metadata internally is
not public.

**(2) What is the minimum text it needs from the PDF, and which page(s)?**

The worker reads at most the **first five pages** (`maxPages = 5`, page indices
0–4) and always reports `totalPages`; it also reads the whole `documentInfo` and
the `Custom` dictionary. The client requires at least one of those pages to carry
text before it will call the service, otherwise it fails immediately with the
"does not contain OCRed text" alert. There is **no minimum character or word count
in the modern client**. For contrast, the historical local implementation required
at least **20 candidate text lines** (after filtering) drawn from up to 15 pages,
and rejected Google's scanned-book boilerplate as the first line.

**(3) How does it treat a PDF whose retrieved candidate only partially matches?**

It accepts it. For PDFs there is no local verification step, no score threshold,
and no re-check of the returned DOI/arXiv ID against the PDF text; the first
translator that returns an item wins, and a title/authors-only service response is
turned straight into a new item (`libraryCatalog = 'Zotero'`). Where several
candidates are offered, the installed `select` handler takes the **first** entry.
The only partial-match safeguards in the open client are EPUB-specific (the
`cleanISBN` mismatch rule) and the pull-based precedence order, which means a
weak title match is only reached after all identifier branches have failed. The
service's own acceptance threshold is not observable.

---

## What apr adopts, what apr rejects, and why

**Adopt**

1. **Identifier-first precedence** (arXiv → DOI → ISBN → title/authors), with each
   branch returning immediately and later branches used only as fallback. It is
   cheap, explainable and already the shape of the reference implementation.
2. **A hard page cap for matching input.** Read a small prefix (5 pages) and send
   the total page count separately for reporting.
3. **A distinct, early "no text layer" outcome.** Failing before any network call
   is both faster and more honest than reporting "no match".
4. **DOI resolution via doi.org content negotiation** with
   `Accept: application/vnd.datacite.datacite+json, application/vnd.crossref.unixref+xml, application/vnd.citationstyles.csl+json`,
   and **ISBN resolution via SRU** (`bath.ISBN=` at `lx2.loc.gov`, `pica.isb=` at
   `sru.k10plus.de`). Note the identifiers themselves are public data points, but
   the requests leak the DOI/ISBN and the user's IP to those third parties.
   apr's own documentation should say so, and the licence/provenance of each
   source should be recorded.
5. **Provenance in the record** (`libraryCatalog`-style field naming the source:
   `DOI.org (Crossref)`, `arXiv.org`, `K10plus ISBN`, …). It is what makes a wrong
   match diagnosable.
6. **Gap-fill merge semantics**: take an abstract or language from the matcher only
   when the authoritative record lacks it. Never overwrite a retrieved field with
   a weaker one.
7. **Undoable recognition.** Zotero keeps the original title/filename and a
   modification guard so "undo" is only offered while the item is untouched. apr
   should keep an equivalent, and be explicit about the guard's conditions.
8. **EPUB-style identifier cross-check.** Where a document supplies its own
   identifier (EPUB RDF, PDF `/Info` when trustworthy), verify the looked-up record
   against it and fall back to local metadata on mismatch. This is the single most
   valuable rule in Zotero's flow and its absence in the PDF path is a defect, not
   a design choice to copy.
9. **Word-level geometry over raw text** when a matcher needs layout (title block,
   authors, footnote/DOI line separated from body text). The 14-field word record
   is a sound data model; apr should define its own equivalent, including the
   fields Zotero reserves but never fills (rotation, underline, colour) only if we
   will actually populate them.

**Reject**

1. **Sending document text to a third-party service as the default path.** Zotero
   sends five pages of word-level text, embedded metadata and the local file name
   to `services.zotero.org`, and documents that this cannot be linked to an account
   and is not logged; the client's own `noAPIKey: true` request supports that claim
   for the request headers, but the content still leaves the machine and the server
   sees the IP. apr is a local tool; this must be opt-in, disclosed in the same
   breath as the feature, and off by default (or implemented against a configurable
   endpoint with the payload documented).
2. **Depending on a closed-source matcher.** The service's algorithm, thresholds and
   endpoint internals are private; nothing can be adopted from it verbatim and no
   behaviour can be promised that depends on it. apr's matcher must be its own,
   with its thresholds in-tree and testable.
3. **Accepting a returned identifier without local verification.** Zotero's PDF
   path trusts the service's DOI/arXiv ID and never checks it against the text it
   just uploaded. apr should require a locally checkable consistency condition and
   mark the record as "unverified match" when it cannot be met.
4. **The `res.ISSN`/`res.issn` guard.** Do not reproduce a field-name
   inconsistency; if apr reads an ISSN from a matcher, it should be one exact key.
5. **Google Scholar scraping, CAPTCHA handling and cookie deletion** (the historical
   path). It is against Scholar's terms, brittle, and no longer used upstream.
6. **Copying any code.** Zotero is AGPL-3.0; apr is MIT. This note is the transfer
   of *ideas and measurements only*. The identifiers, priorities and query strings
   above are interface facts about public endpoints, not code.
7. **Silent file renames.** Zotero renames the attachment from the new parent's
   metadata whenever the auto-rename prefs allow. apr should keep renaming behind
   an explicit, discoverable setting and record the original name for reversal.
8. **Ignoring a password.** Zotero never passes a password to the recognizer
   extraction, so encrypted PDFs degrade to `couldNotRead`. apr should accept a
   password and say what it did.

## Not verified

- The scoring, thresholds, model/algorithm and admission rules of the server-side
  recognizer: repository is private; no public mirror found (GitHub search for a
  Zotero recognizer repository returned only an unrelated third-party plugin).
  Confirmed by the forum answer and the absence of the endpoint in
  `zotero/zotero`.
- The exact response schema of `POST .../recognizer/recognize`. The field list in
  section 1/5 is inferred from the client's consumption of the response, not from a
  published schema.
- Whether the recognizer service reads the `metadata` (PDF `documentInfo`) block it
  receives, and whether it ever prefers it to extracted text.
- The exact `page[2].length` predicate semantics for `recognizePDF.noOCR`
  (section 7): with the worker output I read, the predicate looks unable to be
  zero, yet the alert exists and is user-visible.
- Runtime winner among the ISBN search translators (Open WorldCat / K10plus / LoC).
  Verified only the priority comparator and each translator's own query.
- Whether more ISBN-capable `search` translators exist beyond the three listed
  (I enumerated by reading the translators tree and the named files, not by
  running translator detection).
- The request timeout/size limits applied to the recognizer POST.
- Any minimum *character* count the service requires; no numeric minimum exists in
  the client beyond "one page with text".
- Behaviour of `recognize.url` overrides in real deployments (only seen in tests:
  `Zotero.Prefs.set('recognize.url', 'http://recognizer.test/')`).
- The `report` endpoint's server-side handling and what triggers the UI that calls
  it.
- Whether Zotero ever uses a PDF's embedded metadata as the *attachment* title on
  import (out of scope here; only the recognition flow was traced).
