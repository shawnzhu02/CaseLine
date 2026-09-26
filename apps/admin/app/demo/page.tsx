import { LiveBoard } from "../live/board.tsx";

export const metadata = {
  title: "CaseLine — Live Demo",
  description: "Watch CaseLine's AI intake assistant assess a legal problem in real time and route the caller.",
  robots: { index: false, follow: false },
};

export default function DemoPage() {
  const phone = process.env.DEMO_PHONE_DISPLAY ?? "+1 (484) 968-7497";
  const tel = process.env.DEMO_PHONE_E164 ?? "+14849687497";
  const webrtc = process.env.DEMO_WEBRTC_URL;
  return (
    <div className="demo">
      <style>{DEMO_CSS}</style>
      <header className="demo-hero">
        <p className="demo-kicker">CaseLine</p>
        <h1>One number. One conversation. The right legal help.</h1>
        <p className="demo-lede">
          Callers don&apos;t know what kind of lawyer they need. CaseLine&apos;s AI intake assistant listens,
          updates its understanding with every answer, and routes the caller to a participating firm, while the
          routing decision stays with CaseLine&apos;s own auditable rules.
        </p>
        <ol className="demo-steps">
          <li>Press <b>Play demo call</b>, or call the agent yourself (below) and press <b>Watch the live call</b>.</li>
          <li>Watch the assessment change: Property / Insurance → Personal Injury → Potential Premises Liability.</li>
          <li>See the outcome: CaseLine calls the matched lawyer, or drafts the referral email when the firm is closed.</li>
        </ol>
      </header>

      <LiveBoard mode="public" />

      <section className="demo-try">
        <h2>Try the real agent</h2>
        <p>
          Call <a href={`tel:${tel}`}>{phone}</a>
          {webrtc && (
            <>
              {" "}or <a href={webrtc} target="_blank" rel="noopener noreferrer">talk to it in your browser</a>
            </>
          )}
          . Use invented details only. This page shows only CaseLine&apos;s assessment of the latest call (no names, numbers or words spoken).
        </p>
        <p className="demo-fine">
          Demo participants: &quot;Demo Partner Firm A/B&quot; are placeholder names for team members role-playing
          lawyers; the Massachusetts personal-injury mapping is fictional. CaseLine is an intake and referral
          service, not a law firm, and does not give legal advice.
        </p>
      </section>
    </div>
  );
}

const DEMO_CSS = `
.demo{margin:-20px -16px -60px}
.demo-hero{background:#121211;color:#f2f1ec;padding:40px clamp(16px,4vw,48px) 8px}
.demo-kicker{color:#7fd1b2;font-weight:700;letter-spacing:.12em;text-transform:uppercase;margin:0 0 8px}
.demo-hero h1{font-size:clamp(26px,4vw,42px);line-height:1.15;margin:0 0 14px;max-width:900px}
.demo-lede{color:#c9c7bf;font-size:17px;max-width:820px;margin:0 0 14px}
.demo-steps{color:#c9c7bf;margin:0;padding-left:20px}
.demo .live{margin:0;min-height:auto}
.demo-try{background:#121211;color:#f2f1ec;padding:8px clamp(16px,4vw,48px) 48px}
.demo-try h2{font-size:20px;margin:0 0 8px}
.demo-try a{color:#7fd1b2}
.demo-fine{color:#9d9b92;font-size:13px;max-width:900px}
`;
