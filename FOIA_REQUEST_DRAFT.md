# FOIA Request Draft — Hourly Red-Light & Speed Camera Violation Counts

**Project:** Redunator (Phase 1, Item 4b)
**Drafted:** 2026-11-16 — DRAFT ONLY. Nothing has been filed, sent, or submitted.
**Status:** Send-ready. Gabriel sends this under his own name (see "How to send" below).

---

## Why this request exists

The City of Chicago publishes red-light violations (`spqx-js37`, 1.17M rows) and
speed-camera violations (`hhkd-xvj4`, 504K rows) as **daily counts per camera only**
— no hour of day. Redunator's hourly risk index is therefore an *estimate* built
from proxy signals (crash hours, trip speeds, weather). The underlying violation
events each have a timestamp: the cameras record *when* each violation happened.
Hourly counts per camera would replace the proxy-estimated hourly shape with
observed facts and let the hourly model train on real labels. That is the single
highest-value data improvement available to the project, and it can only come
from a records request — the data is not published anywhere.

This request asks for the **hourly breakdown of the exact same records the City
already publishes daily** — same cameras, same violation definition, just split
into 24 hourly buckets instead of one daily total. It asks for aggregated counts
only. **No personal information is requested** (no plates, no names, no photos,
no video), so no privacy exemption should apply.

---

## How to send (read this first, Gabriel)

1. **Submit through the City's online portal** — GovQA public-records portal at
   **https://chicago.gov/publicrecords** (also linked as
   chicago.gov/SubmitFOIARequest). Paste the letter text from §1 into the request
   field. The portal gives you a GovQA tracking number (format `S######`) — save it.
   The portal is the City's encouraged route and creates a receipt.
2. **Backup / parallel copy by email** to the Chicago Department of Transportation
   FOIA office: **cdotfoia@cityofchicago.org**. Either route alone is a valid
   submission; sending both is belt-and-braces and the portal copy is the one to
   track. If you email, put the full letter text in the **body** of the email, not
   only in an attachment.
3. **Fill in the [bracketed] placeholders** — your full name, mailing address,
   phone, email, and the date — before sending. Nothing else needs editing.
4. **Keep the confirmation.** Save the GovQA number and any email receipt.
   Under Illinois FOIA (5 ILCS 140/3) the City must respond within **5 business
   days** of receipt, and may extend once by up to 5 more business days
   (5 ILCS 140/3(e)) if it tells you why.
5. **Heads-up: your request will be public.** The City posts all FOIA requests and
   requesters' names on its website (the FOIA Request Log on the Chicago Data
   Portal). Your name and the text of this request will be visible to anyone.
6. **If denied or only partly answered**, you can ask for review by the Illinois
   Attorney General's Public Access Counselor — 500 S. Second St., Springfield,
   IL 62701; (877) 299-3642; public.access@ilag.gov — within 60 days, or seek
   judicial review under 5 ILCS 140/11. The letter (§1) already asks the City to
   identify the closest record that *does* exist if the exact record does not,
   which usually avoids a flat denial.
7. **Do not pay anything up front.** The letter requests electronic delivery and a
   fee waiver (see §3). If the City quotes a fee instead, do not pay it without
   checking first — tell Muse and we'll look at whether the quote is right and
   whether narrowing the request (shorter range, fewer cameras) gets it waived.

---

## 1. The letter (copy / paste as-is, filling in the brackets)

> [Gabriel Wright]
> [Your mailing address — street, city, state, ZIP]
> [Your phone number]
> [Your email address]
>
> [Date — e.g. November 16, 2026]
>
> **Via the City of Chicago public-records portal (GovQA) and by email to
> cdotfoia@cityofchicago.org**
>
> Freedom of Information Officer
> Chicago Department of Transportation
> 2 North LaSalle Street, Suite 1110
> Chicago, IL 60602
>
> **Re: Freedom of Information Act Request — Hourly violation counts by camera,
> Automated Enforcement Program (red-light and speed cameras)**
>
> Dear Freedom of Information Officer:
>
> Pursuant to the Illinois Freedom of Information Act, 5 ILCS 140/1 et seq., I
> respectfully request copies of the following public records maintained by the
> Chicago Department of Transportation, including records prepared for the
> Department by its automated-enforcement vendor(s):
>
> **Records requested**
>
> 1. **Hourly red-light camera violation counts**, for each camera in the City's
>    Red Light Camera Enforcement Program, for the period **January 1, 2023
>    through the date of this request**, in the following form — one row per
>    camera per hour, with columns:
>    - `camera_id` — the camera identifier used in the City's published daily
>      dataset (data.cityofchicago.org, "Red Light Camera Violations",
>      dataset `spqx-js37`);
>    - `intersection` — the intersection / location name for that camera;
>    - `date` — calendar date (YYYY-MM-DD);
>    - `hour` — hour of day, 0–23, Central Time, meaning violations occurring
>      in the hour beginning at that time (e.g. hour 15 = 15:00–15:59 CT);
>    - `violation_count` — the number of violations recorded by that camera in
>      that hour.
>    Please include hours with zero violations so the file is a complete
>    camera × date × hour grid. If a complete grid is not feasible, please
>    provide hours with one or more violations only, and state in your response
>    that zero-count hours were omitted.
> 2. **Hourly speed-camera violation counts**, in the same form, for each camera
>    in the City's Automated Speed Enforcement Program (Children's Safety Zones),
>    for the same period, using the camera identifiers from the published daily
>    dataset "Speed Camera Violations" (dataset `hhkd-xvj4`). If speed-camera
>    records are held by a different office, please either forward this portion
>    of the request to that office or tell me which office holds them so I can
>    re-file it there.
>
> **Definition of "violations" — matching the published daily data.** The counts
> I am asking for are the hourly breakdown of the same violations the City
> already publishes as daily totals in datasets `spqx-js37` and `hhkd-xvj4` —
> i.e. violations collected by the camera system and reviewed by the City's
> contractors, regardless of whether a citation was ultimately issued — as the
> daily datasets' own descriptions state. The hourly counts for a given camera
> and date should therefore sum to that camera-day's published daily total.
> If the hourly figures are compiled on a different basis (for example,
> citations issued only, or violations before contractor review), please say so
> and identify the basis.
>
> **Format.** Please provide the records as **machine-readable CSV file(s)**
> (UTF-8, with a header row), delivered electronically — by email to the address
> above or as a download through the public-records portal. One file per program,
> or a single combined file with an added `program` column (`red_light` /
> `speed`), is fine. Please do not provide the data as a PDF, scanned image, or
> printed pages: the records are used for statistical analysis and need to be
> machine-readable. If the data can only be produced in another electronic
> format, please produce it in the native electronic format in which it is
> maintained.
>
> **No personal information requested.** This request is for aggregated counts
> only. I am not requesting license plate numbers, vehicle-owner names or
> addresses, photographs, video, or any other information about individual
> drivers or vehicles. No record responsive to this request should require
> redaction of personal information.
>
> **Why January 1, 2023.** I am requesting three-plus years of data because the
> analysis requires full seasonal coverage across multiple years, and because
> 2023 onward reflects the current post-2020 traffic regime. I am not requesting
> data back to the start of the program in order to keep the request proportionate.
>
> **If the exact records do not exist.** If hourly counts per camera are not
> maintained in this form, please provide the closest record that does exist —
> for example:
> (a) hourly counts for a subset of cameras (e.g. the highest-volume cameras or
>     intersections), or for a shorter date range (e.g. January 1, 2025 to the
>     present, or a single recent 12-month period);
> (b) hourly violation totals aggregated citywide (all cameras combined, by date
>     and hour), if per-camera hourly counts cannot be produced;
> (c) hourly counts aggregated by week or by month (per camera, by hour of day),
>     if daily hourly counts cannot be produced;
> (d) the underlying violation-event records with timestamps, aggregated to the
>     hour and stripped of any personal information, from which hourly counts
>     can be derived; or
> (e) if no hourly record exists at all, the name and description of the system,
>     report, or database query that produces the published daily totals, and
>     the name of the office or vendor that operates it, so that I can submit a
>     precise follow-up request.
> Please do not deny the request outright if a narrower or aggregated version
> can be produced; produce what can be produced and explain what cannot, as
> 5 ILCS 140/7 requires for partial denials.
>
> **Vendor-held records.** To the extent any of these records are held by the
> City's automated-enforcement vendor(s) rather than by the Department itself,
> I note that a "public record" under 5 ILCS 140/2(c) includes records "prepared
> by or for, or having been or being used by, received by, in the possession of
> or under the control of any public body," and that records of a contractor
> directly relating to a governmental function are subject to disclosure. Please
> obtain responsive records from the vendor(s) and produce them. If the
> Department maintains that any responsive vendor-held record is not a public
> record, please identify the record and state the basis for that position.
>
> **Non-commercial request; fee waiver requested.** This is a non-commercial
> request. I am an individual Chicago resident requesting these records for
> public-safety research and analysis — specifically, to build a public,
> data-driven model of when and where red-light and speed violations concentrate,
> so that drivers can be warned about the riskiest times and places. I intend to
> publish the analysis and the resulting risk estimates for public use at no
> charge. I therefore request a waiver or reduction of all fees under
> 5 ILCS 140/6(c): the principal purpose of this request is to access and
> disseminate information regarding the health, safety and welfare of the
> general public, and it is not for the principal purpose of personal or
> commercial benefit. In any event, no fees should be charged for search or
> review of a non-commercial, non-voluminous request, and the first 50 pages of
> black-and-white copies are free under 5 ILCS 140/6(b). Please notify me before
> incurring any fee, and in particular before producing records in a form that
> would generate a charge, so that I can narrow the request if needed.
>
> **Response.** Please respond within five business days as required by
> 5 ILCS 140/3. If you need an extension under 5 ILCS 140/3(e), please tell me
> promptly and state the reason. If any portion of this request is denied, please
> cite the specific exemption relied upon and provide the name and contact
> information for the Public Access Counselor, as required by 5 ILCS 140/9.
>
> Thank you for your attention to this request. I can be reached at the email
> address and phone number above if any clarification would help locate the
> records.
>
> Sincerely,
>
> [Gabriel Wright]
> [Your mailing address]
> [Your email address] · [Your phone number]

---

## 2. Recipient — verified (and the one ambiguity, stated plainly)

**Send to: the Chicago Department of Transportation (CDOT), Freedom of Information
Officer — portal https://chicago.gov/publicrecords, email
cdotfoia@cityofchicago.org, mail 2 North LaSalle St., Suite 1110, Chicago,
IL 60602.**

Why CDOT is the right office:

- Chicago FOIA is **department-specific**: each City department is a separate
  public body responsible for its own records, and requests must go to the
  department that maintains the records. (Stated on every City FOIA page and on
  the City's FOIA guidance.)
- CDOT **runs the Automated Enforcement Program** — both red-light and speed
  cameras — and publishes the program's annual report (most recent checked:
  *2024 Automated Enforcement Program Annual Report*, CDOT). The published
  daily datasets themselves point to CDOT's red-light camera page for program
  information.
- The CDOT FOIA mailbox **cdotfoia@cityofchicago.org** is corroborated by two
  independent Illinois Attorney General documents: Public Access Opinion 23-012
  (September 5, 2023) and PAC determination letter 2022 PAC 72761 (October 5,
  2023), both addressed to CDOT's FOIA Officer at 2 North LaSalle St., Suite
  1110, and both listing `cdotfoia@cityofchicago.org`. The Chicago Headline Club's
  published Chicago FOIA contacts list also gives `CDOTfoia@cityofchicago.org`
  for Transportation.
- A CDOT FOIA response letter from January 2025 (MuckRock file S094087-011725)
  shows CDOT answering requests through the GovQA "Records Center" and signs
  from a CDOT FOIA Officer — confirming the portal route is live for CDOT.

**Caveats (be honest about them):**

- The named FOIA officer has changed over time — Erica Schroeder held the role
  in 2022–2023 (per the AG documents); by January 2025 the role was held by
  G. Rubenstein (Gary.Rubenstein@cityofchicago.org, 312-744-7335). **That is why
  the letter is addressed to the office, not a named person, and why the
  department mailbox — not an individual's email — is the primary address.**
  Do not address it to Schroeder; she may no longer hold the role.
- A direct fetch of CDOT's own FOIA page
  (chicago.gov/city/en/depts/cdot/supp_info/cdot_foia.html) was **blocked with
  HTTP 403 on 2026-11-16**, so the live page could not be re-verified on the day
  of drafting. The mailbox is corroborated by the AG documents and the Headline
  Club list, and the portal route is corroborated by the 2025 CDOT response
  letter — but if the portal rejects the request or the email bounces, re-check
  the City's central FOIA contacts page (chicago.gov/foia) for CDOT's current
  listing before re-sending.

**The one genuine ambiguity — Department of Finance as alternate candidate:**

- The **Department of Finance** is the plausible second candidate: it administers
  citations, fines, and the automated-enforcement revenue account, and its FOIA
  log on the Data Portal contains camera-related requests (e.g. records on
  expenditures of revenue collected by ASE cameras since 2012). If CDOT replies
  that the violation-count data is held by Finance, re-file there.
- **Do not send to Finance first.** The violation *counts* are program data
  generated by CDOT's enforcement operation; Finance holds the *payment*
  side. Sending to CDOT first is correct, and the letter (§1, item 2) already
  asks CDOT to forward or redirect if another office holds part of the records.
- No Finance FOIA email address was verified during drafting, so none is given
  here — look it up on chicago.gov/foia if a re-file becomes necessary.
- **Not** the City Clerk (legislative records only), **not** the Law Department
  (legal records; it defends the City, it does not run the cameras), and **not**
  the Chicago Police Department (CPD handles crash reports, not camera
  violations).

---

## 3. The vendor angle — who operates the cameras, and what FOIA can reach

**Red-light cameras: Modaxo, doing business as Elovate.**
**Speed cameras: Verra Mobility, Inc.**

Verified from CDOT's own *2024 Automated Enforcement Program Annual Report*:

- "The City's two automated enforcement vendors, **Modaxo and Verra Mobility,
  Inc.**, are contractually required to meet specific performance criteria"
  (service level agreements).
- Red-light SLA penalties in the report are assessed against
  "**Modaxo/Elovate**"; the speed-enforcement vendor is identified as
  "**Verra Mobility**."
- Lineage for the red-light contract: awarded October 2013 to **Xerox State &
  Local Solutions** (5-year contract), which **became Conduent, then Modaxo**;
  the contract was extended in October 2018, 2020, and 2022, and in 2024 was
  "further extended to **June 30, 2026**."
- The corporate history checks out independently: Conduent sold its curbside
  management and public-safety businesses to Constellation Software's Modaxo
  division, completed May 1, 2024; the public-safety business now does business
  as **Elovate** (announced by Constellation/Stockwatch; corroborated by
  Delaware DOT's 2026 program report, which calls its vendor "Elovate (also
  known as 'Modaxo' and formerly known as 'Conduent')").

**What this means for the request:**

- **You cannot FOIA the vendor directly.** Modaxo/Elovate and Verra Mobility
  are private companies, not "public bodies" under the Illinois FOIA, so a FOIA
  request sent to them has no legal force. Do not waste time on a vendor FOIA.
- **You do not need to.** Under 5 ILCS 140/2(c), a "public record" includes
  records "prepared by or for" a public body and records "in the possession of
  or under the control of" a public body — and Illinois law treats a contractor's
  records as disclosable where they directly relate to a governmental function;
  a public body cannot contract out a function to evade disclosure. The
  violation counts are the core output of a City enforcement program, produced
  under a City contract, and the City already publishes daily aggregates of
  exactly these records — so the underlying hourly data is unambiguously the
  City's record, wherever the server sits. The letter (§1) makes this point
  expressly and asks CDOT to obtain the records from its vendor(s).
- **One live uncertainty — the red-light contract expired June 30, 2026.**
  The last verified fact is the 2024 extension *to* June 30, 2026. Whether the
  contract was extended again, re-bid, or awarded to a different vendor after
  that date was **not verified** during drafting (the 2025 annual report, if
  published, would say). The letter deliberately does **not** name a vendor as
  the record-holder — it says "its automated-enforcement vendor(s)" — so it
  stays correct whoever holds the contract now. If CDOT's reply names a new
  vendor, note it for the project file; it does not change the request.
- **A voluntary ask to the vendor is possible but low-value.** Gabriel *could*
  write to Elovate/Verra Mobility's public-affairs or privacy contact and ask
  for the same aggregated data as a courtesy, but they have no legal duty to
  answer, and anything they released voluntarily would likely be the same data
  CDOT can be compelled to produce. Only worth trying if CDOT denies the FOIA
  on "vendor-held" grounds — and even then, the correct move is an appeal to
  the Public Access Counselor, not a vendor courtesy letter.

---

## 4. What a partial or denied response is still useful for

The letter already builds in the fallbacks (§1, "If the exact records do not
exist"), ranked here by value to the project:

| Fallback | Value if received |
|---|---|
| **(a) Subset of cameras, or shorter range** (e.g. top-volume cameras, or 2025-01-01 → present) | **High.** Even one full year × all cameras, or the full range × the top 50 cameras, trains and validates an hourly model. The top 10 intersections alone carry most of the ticket volume. |
| **(b) Citywide hourly totals** (all cameras summed, by date and hour) | **Medium-high.** Gives the true citywide hourly shape — the single most important correction to the current estimates (today's citywide shape is inferred from crashes, which peak at 15:00 while trips peak at 18:00; the true violation peak hour is unknown). |
| **(c) Hourly counts by week or month** (per camera, by hour of day) | **Medium.** Gives per-camera hour-of-day profiles (the shape), even without day-level resolution. Enough to replace the crash-derived profiles. |
| **(d) Event-level timestamps, PII-stripped** | **High (equal to the full ask).** If the City will only release raw violation timestamps, hourly counts can be derived locally. Same information, more work. |
| **(e) The system/report/query that produces the daily totals** | **Medium (enables a precise follow-up).** If no hourly record exists, knowing the pipeline (system name, vendor, report name) lets a follow-up request name the exact extract to run. |
| **A "no such record" answer that names the vendor and system** | **Low but not zero.** Confirms where the data lives and who to press next; also establishes the record for a Public Access Counselor appeal. |

What would **not** be useful, and should be pushed back on: a PDF printout of
daily totals (already public), a summary "average violations by hour" with no
per-camera or per-date breakdown (cannot validate per-camera shapes), or a
refusal based on "the vendor holds it" (see §3 — that is not a valid basis
under 5 ILCS 140/2(c), and is appealable).

**If the response is partial, do not re-file the same request.** Reply through
the GovQA thread (or email) accepting the partial production, and file a
*narrowed* follow-up for the missing piece using the fallback list — e.g.
"per your letter of [date], please now produce (a) for cameras [list] for
2025-01-01 to present." Narrowed follow-ups are much harder to deny as
burdensome.

---

## 5. Practical notes for Gabriel

- **Send it under your own name.** FOIA requests must include the requester's
  name, mailing address, phone number, and a description of the records. There
  is no advantage to having anyone else send it, and the request and your name
  will be posted publicly either way (see §"How to send", item 5).
- **It is a non-commercial request — say so.** The letter states this expressly.
  Commercial-purpose requests get slower treatment (21 business days) and can be
  charged search/review fees; non-commercial requests get the 5-day clock and no
  search/review charges. The research purpose stated in the letter (public,
  free, traffic-safety analysis) is accurate — do not change it to anything
  commercial.
- **Fee waiver, in the letter already.** 5 ILCS 140/6(c) requires the requester
  to state the specific purpose and that waiver is in the public interest, which
  the letter does (health/safety/welfare of the general public, not personal or
  commercial benefit). The first 50 pages of black-and-white copies are free by
  statute (5 ILCS 140/6(b)), and electronic records delivered by email/portal
  should not generate a copying charge at all. If the City quotes a fee, do not
  pay it first — check with Muse; the usual fix is narrowing the request.
- **Ask for CSV — the letter already does, twice.** The single most common failure
  mode for data FOIA requests is receiving a PDF. The letter specifies CSV,
  UTF-8, header row, electronic delivery, and "not PDF, scanned image, or
  printed pages." If a PDF arrives anyway, reply asking for the native electronic
  format — Illinois courts have held FOIA requires production in the format in
  which the record is maintained (*Fagel v. IDOT*), so a locked/unusable format
  is appealable.
- **Expected file size.** Three-plus years × ~365 cameras × 24 hours is roughly
  9–10 million rows as a complete grid (several hundred MB as CSV). That is
  large but ordinary for a data extract; if the City balks at size, fallback (a)
  — a shorter range or fewer cameras — is the answer, not abandoning the request.
- **Timeline to expect.** 5 business days for a response; a 5-day extension is
  common for a request of this size; actual production of the file may take
  longer than the response deadline if the City invokes the extension and then
  needs time to run the extract — the response letter should give a production
  date. If nothing arrives within ~3 weeks of submission, follow up through the
  GovQA thread citing the request number.
- **Keep this draft with the project.** If the City replies, save the reply and
  any data file under `~/workspace/redunator/` (e.g. `foia_response_YYYY-MM-DD/`)
  so the hourly model work can use it. The daily datasets the request mirrors
  are `spqx-js37` (red light, from 2014-07-01, ~14-day lag) and `hhkd-xvj4`
  (speed, ends 2026-09-24 in our copy).
- **Nothing has been filed.** This document is a draft only. No account was
  created, no portal submission made, no email sent, no fee paid.

---

## Sources checked (2026-11-16)

| Fact | Source | Checked |
|---|---|---|
| Chicago FOIA is department-specific; each dept. is its own public body | City of Chicago FOIA pages (multiple departments); Project Paper Trail, "How to File a Public Records Request in Chicago" | 2026-11-16 |
| CDOT runs the Automated Enforcement Program (red-light + speed) | CDOT, *2024 Automated Enforcement Program Annual Report* (chicago.gov PDF) | 2026-11-16 |
| CDOT FOIA email `cdotfoia@cityofchicago.org`; office 2 N LaSalle St., Suite 1110 | IL AG Public Access Opinion 23-012 (2023-09-05); PAC letter 2022 PAC 72761 (2023-10-05); Chicago Headline Club FOIA contacts list | 2026-11-16 |
| CDOT FOIA Officer was G. Rubenstein in Jan 2025 (was Erica Schroeder 2022–2023) | CDOT response letter, MuckRock file S094087-011725 (2025-01-31); AG documents above | 2026-11-16 |
| GovQA portal: chicago.gov/publicrecords | City FOIA pages; Project Paper Trail guide; CDOT 2025 response letter (references "the Records Center") | 2026-11-16 |
| Red-light vendor Modaxo/Elovate; speed vendor Verra Mobility | CDOT 2024 Annual Report ("The City's two automated enforcement vendors, Modaxo and Verra Mobility, Inc.") | 2026-11-16 |
| Red-light contract lineage Xerox → Conduent → Modaxo; extended to 2026-06-30 | CDOT 2024 Annual Report | 2026-11-16 |
| Conduent public-safety business sold to Modaxo 2024-05-01, now d/b/a Elovate | Constellation Software announcement (Stockwatch); Delaware DOT 2026 ESSP report | 2026-11-16 |
| Contractor records disclosable where they relate to a governmental function; 5 ILCS 140/2(c) definition | 5 ILCS 140/2(c) (ilga.gov); Illinois FOIA summary (Wikipedia, citing the statute and case law); IL Capitol Group guidance | 2026-11-16 |
| Fee waiver 5 ILCS 140/6(c); first 50 pp. free, $0.15/pg max after; no search/review fees for non-commercial requests | 5 ILCS 140/6 (ilga.gov text); IL OSFM FOIA page; Evanston / Wood River FOIA fee schedules citing the same statute | 2026-11-16 |
| 5-day response; 5-day extension; PAC appeal (877) 299-3642, public.access@ilag.gov | 5 ILCS 140/3, 140/3(e), 140/9.5; CDOT 2025 response letter (states PAC contact) | 2026-11-16 |
| Daily datasets: red-light `spqx-js37` from 2014-07-01, 14-day lag; speed `hhkd-xvj4` | Chicago Data Portal dataset descriptions; Redunator REPORT.md §5 / HOURLY_REPORT.md §1 | 2026-11-16 |
| **NOT re-verified:** live CDOT FOIA page (chicago.gov …/cdot_foia.html) — direct fetch returned HTTP 403 on 2026-11-16 | — | 2026-11-16 |
| **NOT verified:** red-light vendor / contract status after the 2026-06-30 extension expiry; Finance FOIA email address | — | 2026-11-16 |

*This draft is not legal advice. It is a records-request letter prepared from
public sources for Gabriel's own use. If the City denies the request and Gabriel
wants to appeal, the Public Access Counselor (above) is the free first step.*
