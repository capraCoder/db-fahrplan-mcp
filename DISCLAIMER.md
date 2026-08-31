# Disclaimer

**1. Independence.** db-fahrplan-mcp is an independent open-source project. It is not
affiliated with, endorsed by, sponsored by, or supported by Deutsche Bahn AG or any of its
subsidiaries. "Deutsche Bahn", "DB", "ICE", "BahnCard", "Sparpreis" and related marks are
trademarks of Deutsche Bahn AG and are used here solely to describe what the software
connects to (nominative use). The author will rename the package upon substantiated
objection by the rights holder.

**2. Data source and terms of use.** The software retrieves data from publicly reachable
web endpoints of bahn.de that Deutsche Bahn has not documented for third-party use.
Deutsche Bahn's terms of use for bahn.de (Nutzungsbedingungen) restrict automated access
and commercial reuse. By running this software you access bahn.de under your own
responsibility and are solely responsible for complying with those terms and with
applicable law in your jurisdiction. The author does not grant, and cannot grant, any
right to access Deutsche Bahn's systems or data.

**2a. No data shipped, no extraction performed.** The software contains no Deutsche Bahn
data and performs no systematic extraction: each query retrieves only the data the user
asked for, nothing is stored, and nothing is republished. The test fixtures consist of a
handful of single recorded query responses used solely to test the parser.

**3. Intended use.** The software is intended for personal, non-commercial, low-volume
use — an individual asking an AI assistant about their own journeys. It ships with a rate
limiter and an identifying User-Agent for that reason. Running it at scale, reselling its
output, embedding it in a commercial service, or circumventing the rate limiter is outside
the intended use and may violate Deutsche Bahn's terms; the author disclaims any
responsibility for such use.

**4. No guarantee of accuracy or availability.** Timetables, real-time information,
platforms, prices and disruption notices are reproduced as delivered by bahn.de at the
moment of the query and may be incomplete, delayed, or wrong, and the endpoints may change
or stop working at any time without notice. Nothing produced by this software is a ticket,
a booking, a fare quote, or travel advice. Verify anything that matters — connections,
prices, platform — on bahn.de, in the DB Navigator app, or at the station before you rely
on it.

**5. Liability.** The software is provided "as is", without warranty of any kind, as set
out in the MIT License. To the fullest extent permitted by law, the author is not liable
for any loss or damage arising from its use, including but not limited to missed
connections, wrong fares, failed bookings, blocked IP addresses, or claims by third
parties. Liability for intent (Vorsatz), gross negligence (grobe Fahrlässigkeit), and for
injury to life, body or health remains unaffected, as does any liability that cannot be
excluded under applicable law.

**6. Privacy.** The software stores nothing and sends nothing to the author. The station
names, times and traveller options you enter are transmitted to bahn.de, where Deutsche
Bahn's privacy policy applies. If you expose the server over HTTP to others, you become
the operator responsible for their data.

**7. Official distribution.** The only official distribution channels are
https://github.com/capraCoder/db-fahrplan-mcp and the PyPI package `db-fahrplan-mcp`.
Similarly named packages elsewhere are not the author's.

**8. Changes.** The author may withdraw or alter the software at any time, in particular
if Deutsche Bahn objects to its operation.
