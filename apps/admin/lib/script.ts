// Judge demo scripts (from the marketing brief). Invented caller, fictional facts.
export type Line = { speaker: "caller" | "agent"; text: string; outcome?: "connect" | "refer" };

const BASE: Line[] = [
  { speaker: "caller", text: "My house burned down. Everything is gone. I don't know what to do." },
  { speaker: "agent", text: "I'm so sorry. Where did this happen?" },
  { speaker: "caller", text: "Cambridge, Massachusetts." },
  { speaker: "agent", text: "Did you need any medical treatment after the fire?" },
  { speaker: "caller", text: "Yes. I went to the hospital because of smoke inhalation." },
  { speaker: "agent", text: "Were there any known problems with the property before the fire?" },
  { speaker: "caller", text: "Yes. We had already told the landlord that some of the electrical outlets were sparking." },
];

export const SCRIPTS: Record<"connect" | "refer", Line[]> = {
  connect: [
    ...BASE,
    { speaker: "agent", text: "I've identified the type of legal help that may be relevant. Would you like me to connect you?" },
    { speaker: "caller", text: "Yes, please.", outcome: "connect" },
  ],
  refer: [
    ...BASE,
    { speaker: "agent", text: "The firm can't take a live call right now. May I send them a summary so they can follow up with you?" },
    { speaker: "caller", text: "Yes, please send it.", outcome: "refer" },
  ],
};
