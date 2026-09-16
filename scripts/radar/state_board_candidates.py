"""Candidate URLs for the fifty states' central government job boards.

Candidates, not coordinates. Every one of these is a starting URL to probe,
not a verified feed -- `discover_state_boards.py` is what decides which ones
JobDesk can actually read, and only the ones that clear all three gates get
written into `jobdesk/radar/data/state_boards.toml`.

They are here as a separate list because a state's HR department moves its
board every few years and the probe has to be re-runnable against a list
somebody edited, without touching the prober.

Where a state runs several boards, the one listed is the central classified
service -- the index an ordinary applicant is pointed at. University systems,
individual agencies and the legislature each run their own and are out of
scope: a statewide index is already thousands of postings.
"""

CANDIDATES = {
    "AK": ("Alaska", "https://www.governmentjobs.com/careers/Alaska"),
    "AL": ("Alabama", "https://www.personnel.alabama.gov"),
    "AR": ("Arkansas", "https://arcareers.arkansas.gov"),
    "AZ": ("Arizona", "https://azstatejobs.azdoa.gov"),
    "CA": ("California", "https://www.calcareers.ca.gov"),
    "CO": ("Colorado", "https://www.governmentjobs.com/careers/colorado"),
    "CT": ("Connecticut", "https://www.jobapscloud.com/CT/"),
    "DC": ("District of Columbia", "https://careers.dc.gov"),
    "DE": ("Delaware", "https://statejobs.delaware.gov"),
    "FL": ("Florida", "https://jobs.myflorida.com"),
    "GA": ("Georgia", "https://careers.georgia.gov"),
    "HI": ("Hawaii", "https://www.governmentjobs.com/careers/hawaii"),
    "IA": ("Iowa", "https://das.iowa.gov/human-resources/state-employment"),
    "ID": ("Idaho", "https://www.governmentjobs.com/careers/idaho"),
    "IL": ("Illinois", "https://illinois.jobs2web.com"),
    "IN": ("Indiana", "https://workforindiana.in.gov"),
    "KS": ("Kansas", "https://jobs.ks.gov"),
    "KY": ("Kentucky", "https://careers.ky.gov"),
    "LA": ("Louisiana", "https://www.governmentjobs.com/careers/louisiana"),
    "MA": ("Massachusetts", "https://www.mass.gov/topics/state-jobs"),
    "MD": ("Maryland", "https://www.jobapscloud.com/MD/"),
    "ME": ("Maine", "https://www.maine.gov/bhr/state-jobs"),
    "MI": ("Michigan", "https://www.governmentjobs.com/careers/michigan"),
    "MN": ("Minnesota", "https://mn.gov/mmb/careers/"),
    "MO": ("Missouri", "https://mocareers.mo.gov"),
    "MS": ("Mississippi", "https://www.mspb.ms.gov"),
    "MT": ("Montana", "https://statecareers.mt.gov"),
    "NC": ("North Carolina", "https://www.governmentjobs.com/careers/northcarolina"),
    "ND": ("North Dakota", "https://www.nd.gov/omb/public/careers"),
    "NE": ("Nebraska", "https://statejobs.nebraska.gov"),
    "NH": ("New Hampshire", "https://www.nh.gov/hr/employment-opportunities.htm"),
    "NJ": ("New Jersey", "https://www.state.nj.us/csc/seekers/jobs/"),
    "NM": ("New Mexico", "https://www.spo.state.nm.us"),
    "NV": ("Nevada", "https://hr.nv.gov/Sections/Employment/"),
    "NY": ("New York", "https://statejobs.ny.gov"),
    "OH": ("Ohio", "https://careers.ohio.gov"),
    "OK": ("Oklahoma", "https://oklahoma.gov/jobs.html"),
    "OR": ("Oregon", "https://www.oregon.gov/jobs"),
    "PA": ("Pennsylvania", "https://www.employment.pa.gov"),
    "RI": ("Rhode Island", "https://www.governmentjobs.com/careers/ri"),
    "SC": ("South Carolina", "https://www.governmentjobs.com/careers/sc"),
    "SD": ("South Dakota", "https://bhr.sd.gov/job-seekers/"),
    "TN": ("Tennessee", "https://www.tn.gov/careers.html"),
    "TX": ("Texas", "https://capps.taleo.net/careersection/ex/jobsearch.ftl"),
    "UT": ("Utah", "https://statejobs.utah.gov"),
    "VA": ("Virginia", "https://www.jobs.virginia.gov"),
    "VT": ("Vermont", "https://careers.vermont.gov"),
    "WA": ("Washington", "https://www.careers.wa.gov"),
    "WI": ("Wisconsin", "https://wisc.jobs"),
    "WV": ("West Virginia", "https://personnel.wv.gov"),
    "WY": ("Wyoming", "https://www.governmentjobs.com/careers/wyoming"),
}
