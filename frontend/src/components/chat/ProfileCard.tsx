import { Card, CardContent, CardHeader } from "../ui/card";
import { User } from "../ui/icons";

interface Profile {
  nickname: string;
  ai_name: string;
  ai_emoji: string;
  platform: string;
  email?: string | null;
  setup_stage: number;
}

interface ProfileCardProps {
  profile?: Profile;
}

const infoItems = (profile?: Profile) => [
  { label: "昵称", value: profile?.nickname },
  { label: "AI 名称", value: profile?.ai_name },
  { label: "AI 表情", value: profile?.ai_emoji },
  { label: "平台", value: profile?.platform },
  { label: "邮箱", value: profile?.email },
  { label: "引导阶段", value: profile?.setup_stage?.toString() },
];

export function ProfileCard({ profile }: ProfileCardProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-surface text-content-secondary border border-border">
          <User size={18} />
        </div>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-content truncate">{profile?.nickname || "Guest"}</h2>
          <p className="text-xs text-content-tertiary truncate">{profile?.email || "未绑定邮箱"}</p>
        </div>
      </div>
      
      <div className="space-y-1 pt-1">
        {infoItems(profile).filter(i => ["AI 名称", "AI 表情"].includes(i.label)).map(({ label, value }) => (
          <div
            key={label}
            className="flex items-center justify-between py-1 px-1"
          >
            <span className="text-xs text-content-tertiary">{label}</span>
            <span className="text-xs font-medium text-content">{value || "-"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
