"""Assign a primary topical category to every work in library.db.

Rule-based: each rule is a (regex on lowered title, optional author keyword, category)
tuple. First matching rule wins. The category is written into `works.subjects`
(comma-separated; we keep one primary topic only for now).

Re-runnable: overwrites the existing primary category on each row so editing
rules and re-running is cheap.

Usage:
    python3 scripts/categorize_books.py            # apply, report unmatched
    python3 scripts/categorize_books.py --dry-run  # show what would change
    python3 scripts/categorize_books.py --report   # write reports/categories.md
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "library.db"
DEFAULT_MD = REPO / "reports/categories.md"

# Order matters: more specific rules first.
# Tuple shape: (category, title_regex, [author_substring]).
#  - title_regex is matched (re.search) against the lowercased title
#  - author_substring (optional, lowercased substring) is required if present
RULES = [
    # ---- Finance: test prep (most specific) -----------------------------------
    ("Finance: Test Prep (CFA / CAIA)", r"\bcfa\b|\bcaia\b|schweser|kaplan"),
    ("Finance: Test Prep (CFA / CAIA)", r"alternative investments", "kazemi"),
    ("Finance: Test Prep (CFA / CAIA)", r"alternative investments", "chambers"),

    # ---- Finance: quant / fixed income / derivatives --------------------------
    ("Finance: Quant & Derivatives",
     r"option(s)? (volatility|pricing|futures|strateg)|volatility surface|"
     r"derivatives|risk parity|stochastic calculus|kelly capital|"
     r"financial engineering|fixed income (analy|mathemat|securit)|"
     r"bond market|distressed debt|private debt|yield book|active portfolio|"
     r"investment banking|leveraged finance|empirical market|quant(itative)? "
     r"(equity|risk|finance|job)|algorithmic (short|trading)|practitioner.s guide|"
     r"convertible|asset pricing|fractal|chaos and order in the capital|"
     r"global macro trading|efficiently inefficient|fixed income analyt|"
     r"financial data engineering|financial data and|expected returns|"
     r"financial modeling|implementing financial models|trader construction|"
     r"machine trading|quant"),
    ("Finance: Quant & Derivatives", r"\bvolatility\b"),
    ("Finance: Quant & Derivatives", r"\bhedge funds?\b"),
    ("Finance: Quant & Derivatives", r"\bmarket microstructure"),

    # ---- Finance: corporate / valuation / M&A ---------------------------------
    ("Finance: Corporate & Valuation",
     r"corporate finance|valuation|fundamentals of corporate|mergers & acquisitions|"
     r"financial statement|cash flow reporting|credit risk|interpretation of "
     r"financial|investment valuation|dark side of valuation|four cornerstones|"
     r"value: the four|security analysis|insurance and behavioral|case in point|"
     r"private equity|big orbit|comp(?:endium)?|practitioner.s guide to mergers"),

    # ---- Finance: investing & markets (popular) -------------------------------
    ("Finance: Investing & Markets",
     r"intelligent investor|random walk down|stock market|most important thing|"
     r"market cycle|dhandho|fortune.s formula|beat the (dealer|market)|"
     r"a man for all markets|fortune|essential drucker|safe haven|"
     r"this time is different|mortgage securit|economic puppetmaster|"
     r"investing in|crisis|alchemy of finance|soros on soros|inside delta force|"
     r"lords of finance|how to decide|thinking in bets|superforecasting|"
     r"company of heroes|economic indicators|2016 stocks|"
     r"new lombard street|extraordinary popular delusions|triumph of the optim|"
     r"essential tversky|stock market genius|outperform|outperformer|"
     r"value method|low.risk value|moneyball|hard thing about hard|alchemy"),
    ("Finance: Investing & Markets", r"^anduril thesis$"),
    ("Finance: Investing & Markets", r"safe haven"),

    # ---- Career & job hunting -------------------------------------------------
    ("Career & Job Hunt",
     r"parachute|fast track|career as a wall street|quant job interview|"
     r"ace the data science|five steps to the epiphany|four steps to the epiphany|"
     r"hbr guide to (changing your career)|the family nest egg"),

    # ---- Math & Statistics ----------------------------------------------------
    ("Math & Statistics",
     r"calculus|linear algebra|differential equation|multivariable|probability|"
     r"first course in probability|measure theory|mathematics for finance|"
     r"mathematical (methods|foundations)|mathematics:|mathematics for|"
     r"enjoyment of math|mathematics$|quantum mechanics|game theory|"
     r"playing for real|essence of sql|q tips|geographical pivot of|"
     r"qoo|frequently asked questions in quantitative|dragon fire method|"
     r"a quantitative primer on|complete strategy collection"),
    ("Math & Statistics", r"\bcalculus\b"),
    ("Math & Statistics", r"^mathematics"),
    ("Math & Statistics", r"\benjoyment of math\b"),
    ("Math & Statistics", r"\bprobability\b"),

    # ---- CS, programming, algorithms ------------------------------------------
    ("Computer Science & Programming",
     r"^think (python|bayes|stats|complexity)|hitchhiker.s guide to python|"
     r"pragmatic programmer|numerical recipes|c\+\+ primer|intro to python|"
     r"automate the boring stuff|hands.on programming with r|r cookbook|"
     r"python pocket reference|computer science|operating systems|introduction "
     r"to algorithms|theory of computation|artificial intelligence: a modern|"
     r"cryptography algorithms|mathematics of secrets|python machine learning|"
     r"daily coding problem|decision trees & random forests"),

    # ---- Data Science / ML / AI -----------------------------------------------
    ("Data Science, ML & AI",
     r"pattern recognition|machine learning|deep learning|kaggle|"
     r"data science|data analysis|practical statistics for data|"
     r"introduction to machine learning|python for algorithmic trading|"
     r"python for finance|python for data analysis|all the math you missed|"
     r"essential math for data|fundamentals of data engineering|"
     r"generative deep|hands.on large language|quick start guide to large|"
     r"causal inference|ai product manager|competing in the age of ai|"
     r"football analytics with"),
    ("Data Science, ML & AI", r"^think (machine|deep|ml)"),

    # ---- Productivity & habits ------------------------------------------------
    ("Productivity & Habits",
     r"atomic habits|tiny habits|getting things done|the now habit|"
     r"deep work|essentialism|miracle morning|feel good productivity|"
     r"the talent code|talent code|the success principles|compound effect|"
     r"daily pressfield|the war of art|pivot:|pivot$|do the work|turning pro|"
     r"the slight edge"),

    # ---- Self-help / Personal development -------------------------------------
    ("Personal Development & Self-Help",
     r"power of full engagement|the corporate athlete|personal credo|"
     r"leading with character|the power of story|moonwalking with einstein|"
     r"think and grow rich|how to win friends|mindset|grit|"
     r"unlimited memory|the memory book|drive: the surprising truth|"
     r"the big leap|the 80/20 principle|jeff bezos|man.s search for meaning|"
     r"as a man thinketh|you were born for this|ikigai|public speaking|"
     r"the success principles workbook|breakthrough rapid reading|how to be a super reader|"
     r"speed reading|evelyn wood|build a better brain|gorilla mindset|"
     r"prairie fire|chosen soldier|to hell and back|the right stuff|"
     r"poliquin principles|german body comp|75 hard|knee ability|"
     r"strong advice|charisma myth|psycho.cybernetics|developing talent|"
     r"steal like an artist|show your work|keep going|the new toughness training|"
     r"taming your gremlin|how i found freedom|the world according to mister|"
     r"world according to mister rogers|courage to be (disliked|happy)|"
     r"family nest egg|its never too late|never too late to begin|artist.s way|"
     r"feeding the monster|gremlin|every good endeavor|inside the global economy|"
     r"the success principles|maps of meaning|12 rules for life|entitlement cure|"
     r"personality isn.t permanent|no more mr. nice guy|dave ramsey|"
     r"not caring what other people|all the evil of this world|internal family|"
     r"feel good|build:|build an unorthodox|good and beautiful god|"
     r"hidden trillion dollar|new toughness|love does|how to read a book|"
     r"the now habit|tribe of mentors|tools of titans|4.hour workweek|"
     r"the strenght|strengthsfinder|maxwell leadership bible|"
     r"a year with c\.s\. lewis|spiritual exercises|bhagavad gita|"
     r"letters to a young catholic|how to fail at almost everything|"
     r"almanack of naval|practicing the power of now|essential drucker|"
     r"who moved my cheese|7 habits|four agreements|straitjacket society|"
     r"ho.oponopono|the entrepreneur.s weekly nietzsche|art of seduction|"
     r"the success principles|crucial conversations|hello, my name is awesome|"
     r"steal like|do the work|turning pro|the talent code|the four agreements|"
     r"\byou.\b|\bself.respect\b|wisdom of sundays|making|impossible to ignore|"
     r"radio.s greatest|rush on the radio|see, i told you so|the way things ought|"
     r"^drive$|drive: the surprising|^build$|^pivot$|^pivot:"),

    # ---- Marketing, Sales & Persuasion ----------------------------------------
    ("Marketing, Sales & Persuasion",
     r"crossing the chasm|killer visual|sketchnote|art of visual notetaking|"
     r"\binfluence\b|^pre.suasion|the ultimate marketing|the ultimate sales|"
     r"pitch the perfect|hello my name|exposing the magic of design|"
     r"actionable gamification|impossible to ignore|art of seduction|"
     r"the 48 laws of power|blue ocean strategy|the most dangerous business|"
     r"the rational optimist|how innovation works|making|"
     r"the art of startup fundraising|safe haven"),

    # ---- Business strategy / management / entrepreneurship --------------------
    ("Business: Strategy & Management",
     r"the lean startup|startup cxo|the startup owner|the four steps to the epiphany|"
     r"five steps to the epiphany|crossing the chasm|hard thing about|"
     r"high output management|the four steps|zero to one|how to write a great "
     r"business plan|understanding michael porter|case in point|"
     r"the (lessons|art) of (history|war)|complete strategy collection|"
     r"the personal credo|the charisma myth|the only way to win|"
     r"corporate athlete|build:|build an unorthodox|"
     r"sol price|the anduril thesis|conspiracies of the ruling class|"
     r"the ruling class|liberty and tyranny|coming apart|road to serfdom|"
     r"^last stands"),

    # ---- Communication, writing, story ----------------------------------------
    ("Writing & Communication",
     r"on writing|bird by bird|strunk|elements of style|the anatomy of story|"
     r"the war of art|the artist.s way|its never too late to begin|"
     r"public speaking for success|hello, my name is awesome|"
     r"the art of self respect|killer visual|sketchnote handbook|"
     r"sketchnote workbook|the art of visual notetaking|"
     r"power of story|the personal credo|the charisma myth|"
     r"hidden trillion dollar|impossible to ignore"),

    # ---- Leadership -----------------------------------------------------------
    ("Leadership",
     r"team of teams|leadership bible|^chosen soldier|inside delta force|"
     r"checklist manifesto|every good endeavor|leadership beyond reason|"
     r"5 laws of trust|five laws of trust|liberty and tyranny|"
     r"high output management|the only way to win|maxwell leadership|"
     r"the power of full engagement|the personal credo|leading with character|"
     r"the corporate athlete|^team of teams"),

    # ---- Psychology & behavioral ---------------------------------------------
    ("Psychology & Behavioral Science",
     r"thinking, fast and slow|^influence\b|^pre.suasion|"
     r"^drive$|drive: the surprising|^mindset$|"
     r"i see satan|things hidden since|"
     r"the (hidden|misb)|misbehaviour of markets|misbehavior of markets|"
     r"how to decide|thinking in bets|annie duke|expected returns|"
     r"the courage to be disliked|the courage to be happy|"
     r"how to read a book|^you²|^you 2|you squared|quantum leap strategy"),

    # ---- Sports & Fitness -----------------------------------------------------
    ("Sports, Fitness & Health",
     r"squat bible|knee ability|becoming a supple|rebuilding milo|"
     r"75 hard|mathletics|the book: playing|book: playing the percentages|"
     r"how i play golf|play better golf|modern fundamentals of golf|"
     r"flexible dieting|german body comp|poliquin principles|"
     r"the ultimate book of sports|encyclopedia of modern bodybuilding|"
     r"trading bases|knee ability|the men of the fighting"),

    # ---- Philosophy & religion ------------------------------------------------
    ("Philosophy & Religion",
     r"art of living|epictetus|man.s search for meaning|"
     r"the spiritual exercises|bhagavad gita|"
     r"letters to a young catholic|year with c\.s\. lewis|"
     r"things hidden since the foundation|i see satan fall|"
     r"good and beautiful god|the beginning of infinity|"
     r"complete strategy collection|holy bible|"
     r"^you were born|practicing the power of now"),

    # ---- History, geopolitics, current affairs --------------------------------
    ("History, Politics & Current Affairs",
     r"lessons of history|geographical pivot|reagan|ronald reagan|"
     r"specter of communism|epoch times|"
     r"american war on election|seth keshel|"
     r"complete personal memoirs|ulysses s\. grant|"
     r"how the specter|how innovation works|"
     r"safari (?:to mecca|of)|sarah palin|going rogue|inventing mark twain|"
     r"the rational optimist|how innovation works|"
     r"feeding the monster|^the right stuff|^lone survivor|"
     r"^last stands|^blitz$|to hell and back|coming apart|"
     r"^liberty and tyranny|^conspiracies of the ruling|^the ruling class|"
     r"see, i told you so|the way things ought to be|radio.s greatest|"
     r"rush on the radio|^road to serfdom|rush limbaugh"),

    # ---- Memoir / biography (when not better categorized above) ---------------
    ("Memoir & Biography",
     r"audie murphy|^prairie fire|chosen soldier|the right stuff|"
     r"^lone survivor|^last stands|^inside delta|"
     r"shadow divers|^inventing mark|sol price|tribe of mentors|"
     r"4.hour|tools of titans|almanack of naval|"
     r"american war on election"),

    # ---- Reading, memory, learning -------------------------------------------
    ("Reading, Memory & Learning",
     r"speed reading|how to be a super reader|breakthrough rapid|"
     r"the evelyn wood|unlimited memory|the memory book|moonwalking with einstein|"
     r"^as a man thinketh|^how to read a book"),

    # ---- Fiction & classics ---------------------------------------------------
    ("Fiction & Classics",
     r"dr\. jekyll|jekyll and mr\. hyde|cain.s jawbone"),

    # ---- Economics (separate from finance — broad macro/micro & textbooks) ---
    ("Economics",
     r"^basic economics|principles of macroeconomics|principles of economics|"
     r"^price setting$|economics of information|"
     r"macroeconomic patterns|applied financial macroeconomics|"
     r"financial decisions and markets"),

    # ---- Science (physics / biology / popular science) -----------------------
    ("Science",
     r"feynman lectures|exercises for the feynman|^genome$|"
     r"mathematical foundations of quantum|mathematical methods of physics"),

    # ---- Catch-up rules for remaining unmatched titles -----------------------
    ("Finance: Quant & Derivatives",
     r"crash course in options|analysis of financial time series|"
     r"asset management|options demystified|options as a strategic|"
     r"statistical models and methods|techniques of financial analysis|"
     r"misbehaviour of markets|misbehavior of markets|mis.behaviour of markets|"
     r"famous first bubbles|the little book that still beats|"
     r"what works on wall street|poor charlie.s almanack|"
     r"financial shenanigans|quality of earnings|"
     r"high performing investment teams|heard on the street"),
    ("Business: Strategy & Management",
     r"anatomy of the swipe|ethics in practice|bulletproof problem solving|"
     r"identifying project risk|the three laws of performance|"
     r"visual collaboration"),
    ("Marketing, Sales & Persuasion",
     r"^decoded$|visual collaboration"),
    ("History, Politics & Current Affairs",
     r"^blitz"),
    ("Personal Development & Self-Help",
     r"^algorithms to live by|^an anatomy of inspiration|^boundaries$|"
     r"^faster than normal|^make anything happen|^nlp:|"
     r"^rich dad poor dad|^success affirmations|^the big book of awards|"
     r"^the great mental models|^the little book of talent|"
     r"^the step.by.step guide to prepare|^the whole man$"),
    ("Philosophy & Religion",
     r"^the hero with a thousand faces"),
]


def assign_category(title: str, author: str) -> str | None:
    t = (title or "").lower()
    a = (author or "").lower()
    for rule in RULES:
        category, pat = rule[0], rule[1]
        author_kw = rule[2] if len(rule) > 2 else None
        if author_kw and author_kw not in a:
            continue
        if re.search(pat, t):
            return category
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--md", type=Path, default=DEFAULT_MD)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--report-only", action="store_true",
                   help="just generate the markdown report; do not write to DB")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    works = conn.execute("SELECT id, title, author_display, subjects FROM works ORDER BY title").fetchall()

    assigns = {}
    unmatched = []
    for w in works:
        cat = assign_category(w["title"], w["author_display"])
        if cat is None:
            unmatched.append((w["id"], w["title"], w["author_display"]))
        else:
            assigns[w["id"]] = cat

    if not args.dry_run and not args.report_only:
        for wid, cat in assigns.items():
            conn.execute("UPDATE works SET subjects = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (cat, wid))
        conn.commit()

    counts = Counter(assigns.values())
    print(f"works: {len(works)}")
    print(f"  categorized: {len(assigns)}")
    print(f"  unmatched:   {len(unmatched)}")
    print()
    print("Distribution:")
    for c, n in counts.most_common():
        print(f"  {n:>4} {c}")
    if unmatched:
        print()
        print(f"First 25 unmatched (please add rules):")
        for wid, t, a in unmatched[:25]:
            print(f"  [{wid:>3}] {t} — {a}")

    # Markdown report grouped by category
    args.md.parent.mkdir(parents=True, exist_ok=True)
    by_cat: dict[str, list] = defaultdict(list)
    for w in works:
        cat = assigns.get(w["id"], "(unmatched)")
        by_cat[cat].append(w)
    lines = [f"# Books by Category ({len(works)} works)\n"]
    for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
        lines.append(f"\n## {cat} ({len(by_cat[cat])})\n")
        for w in sorted(by_cat[cat], key=lambda x: x["title"]):
            lines.append(f"- **{w['title']}** — {w['author_display'] or '?'}")
    args.md.write_text("\n".join(lines))
    print(f"\nWrote {args.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
