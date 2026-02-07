import { useState } from "react";
import { Zap, Calendar, Wallet, Link2 } from "../../components/ui/icons";
import { SkillsPanel } from "../../components/skills/SkillsPanel";
import { CalendarPanel } from "../../components/chat/CalendarPanel";
import { LedgerStatsCard } from "../../components/chat/LedgerStatsCard";
import { LedgerListCard } from "../../components/chat/LedgerListCard";
import { BindingCard } from "../../components/chat/BindingCard";

interface RightInfoPanelProps {
  token: string | null;
  stats: any;
}

type TabKey = "skills" | "calendar" | "ledger" | "binding";

const TABS: { key: TabKey; label: string; icon: React.ElementType }[] = [
  { key: "skills", label: "技能", icon: Zap },
  { key: "calendar", label: "日历", icon: Calendar },
  { key: "ledger", label: "账单", icon: Wallet },
  { key: "binding", label: "绑定", icon: Link2 },
];

export function RightInfoPanel({ token, stats }: RightInfoPanelProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("skills");

  return (
    <div className="flex flex-col h-full bg-surface-card border-l border-border">
      {/* Tabs Header */}
      <div className="flex items-center p-2 border-b border-border gap-1 overflow-x-auto no-scrollbar shrink-0">
        {TABS.map((tab) => {
          const isActive = activeTab === tab.key;
          const Icon = tab.icon;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`
                flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap flex-1 justify-center
                ${
                  isActive
                    ? "bg-content text-surface"
                    : "text-content-secondary hover:bg-surface-hover hover:text-content"
                }
              `}
            >
              <Icon size={16} />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-y-auto p-4 min-h-0">
        {activeTab === "skills" && <SkillsPanel token={token} />}
        
        {activeTab === "calendar" && <CalendarPanel token={token} />}
        
        {activeTab === "ledger" && (
          <div className="space-y-4">
            <LedgerStatsCard stats={stats} />
            <LedgerListCard token={token} />
          </div>
        )}
        
        {activeTab === "binding" && (
          <div className="max-w-lg mx-auto">
            <BindingCard token={token} />
          </div>
        )}
      </div>
    </div>
  );
}
