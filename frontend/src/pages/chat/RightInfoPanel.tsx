import { useEffect, useMemo, useState } from "react";
import { Bot, Calendar, Link2, Wallet, Zap } from "../../components/ui/icons";
import { BindingCard } from "../../components/chat/BindingCard";
import { CalendarPanel } from "../../components/chat/CalendarPanel";
import { LedgerListCard } from "../../components/chat/LedgerListCard";
import { LedgerStatsCard } from "../../components/chat/LedgerStatsCard";
import { SkillsPanel } from "../../components/skills/SkillsPanel";

interface RightInfoPanelProps {
  token: string | null;
  stats: any;
  executionDebug?: Record<string, unknown> | null;
  showExecutionPanel?: boolean;
}

type TabKey = "ledger" | "calendar" | "skills" | "binding" | "execution";

const TABS: { key: TabKey; label: string; icon: React.ElementType }[] = [
  { key: "ledger", label: "账单", icon: Wallet },
  { key: "calendar", label: "日程", icon: Calendar },
  { key: "skills", label: "技能", icon: Zap },
  { key: "binding", label: "绑定", icon: Link2 },
  { key: "execution", label: "执行", icon: Bot },
];

export function RightInfoPanel({
  token,
  stats,
  executionDebug,
  showExecutionPanel = true,
}: RightInfoPanelProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("ledger");
  const visibleTabs = useMemo(
    () => (showExecutionPanel ? TABS : TABS.filter((tab) => tab.key !== "execution")),
    [showExecutionPanel]
  );
  const routeIntent =
    executionDebug && typeof executionDebug.route_intent === "string"
      ? executionDebug.route_intent
      : "";

  useEffect(() => {
    if (!showExecutionPanel && activeTab === "execution") {
      setActiveTab("ledger");
    }
  }, [activeTab, showExecutionPanel]);

  return (
    <div className="flex h-full flex-col border-l border-border bg-surface-card">
      <div className="no-scrollbar flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border p-2">
        {visibleTabs.map((tab) => {
          const isActive = activeTab === tab.key;
          const Icon = tab.icon;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              aria-label={`切换到${tab.label}标签`}
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
        {showExecutionPanel && activeTab === "execution" && (
          <div className="space-y-4">
            {!executionDebug ? (
              <div className="rounded-xl border border-border bg-surface p-4 text-sm text-content-secondary">
                暂无执行摘要。发送消息后，这里仅展示路由与状态；中间执行细节已写入后端日志。
              </div>
            ) : (
              <>
                <div className="rounded-xl border border-border bg-surface p-4">
                  <div className="text-xs text-content-tertiary">路由节点</div>
                  <div className="mt-1 text-sm font-medium text-content">{routeIntent || "unknown"}</div>
                </div>
                <div className="rounded-xl border border-border bg-surface p-4">
                  <div className="text-xs text-content-tertiary">执行状态</div>
                  <div className="mt-2 text-sm text-content-secondary">
                    中间执行结果（计划与工具轨迹）已记录到后端日志，不在前端展示。
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
