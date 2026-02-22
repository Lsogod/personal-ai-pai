import { useState } from "react";
import { Calendar, Link2, Wallet, Zap } from "../../components/ui/icons";
import { BindingCard } from "../../components/chat/BindingCard";
import { CalendarPanel } from "../../components/chat/CalendarPanel";
import { LedgerListCard } from "../../components/chat/LedgerListCard";
import { LedgerStatsCard } from "../../components/chat/LedgerStatsCard";
import { SkillsPanel } from "../../components/skills/SkillsPanel";

interface RightInfoPanelProps {
  token: string | null;
  stats: any;
}

type TabKey = "ledger" | "calendar" | "skills" | "binding";

const TABS: { key: TabKey; label: string; icon: React.ElementType }[] = [
  { key: "ledger", label: "账单", icon: Wallet },
  { key: "calendar", label: "日程", icon: Calendar },
  { key: "skills", label: "技能", icon: Zap },
  { key: "binding", label: "绑定", icon: Link2 },
];

export function RightInfoPanel({ token, stats }: RightInfoPanelProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("ledger");

  return (
    <div className="flex h-full flex-col border-l border-border bg-surface-card">
      <div className="no-scrollbar flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border p-2">
        {TABS.map((tab) => {
          const isActive = activeTab === tab.key;
          const Icon = tab.icon;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={[
                "flex flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-content text-surface"
                  : "text-content-secondary hover:bg-surface-hover hover:text-content",
              ].join(" ")}
            >
              <Icon size={16} />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {activeTab === "skills" && <SkillsPanel token={token} />}
        {activeTab === "calendar" && <CalendarPanel token={token} />}
        {activeTab === "ledger" && (
          <div className="space-y-4">
            <LedgerStatsCard stats={stats} token={token} />
            <LedgerListCard token={token} />
          </div>
        )}
        {activeTab === "binding" && (
          <div className="mx-auto max-w-lg">
            <BindingCard token={token} />
          </div>
        )}
      </div>
    </div>
  );
}
