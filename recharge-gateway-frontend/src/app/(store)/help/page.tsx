"use client";

import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  ArrowUpRight,
  ChevronDown,
  FileJson,
  KeyRound,
  MessageCircle,
  PackageCheck,
  ReceiptText,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { CopyButton } from "@/components/CopyButton";
import { Modal } from "@/components/Modal";
import { BRAND } from "@/config/brand";
import { api } from "@/lib/api";
import type { BrandInfo } from "@/lib/types";

const ICONS = {
  receipt: ReceiptText,
  key: KeyRound,
  json: FileJson,
  alert: AlertCircle,
  package: PackageCheck,
  shield: ShieldCheck,
} as const;

type Topic = {
  id: string;
  number: string;
  title: string;
  desc: string;
  icon: keyof typeof ICONS;
  paragraphs: string[];
  faqs: { q: string; a: string }[];
};

const TOPICS: Topic[] = [
  {
    id: "orders",
    number: "01",
    title: "查看订单与获取卡密",
    desc: "登录订单中心查看支付状态与交付信息。",
    icon: "receipt",
    paragraphs: [
      "支付完成后，系统会自动跳转到交付结果页，同时把 MOLI 激活码或成品号账密写入你的订单。",
      "下单时填写的邮箱和密码就是订单账户。之后打开「我的订单」登录，即可查看完整交付内容。首次下单会自动创建账户。",
    ],
    faqs: [
      { q: "订单交付内容在哪里？", a: "使用下单邮箱和密码登录「我的订单」，在对应订单详情中查看激活码或账密。" },
      { q: "忘记订单密码怎么办？", a: "联系在线客服并提供订单编号，核实后可为你的账户重置密码；旧登录凭证会立即失效。" },
      { q: "订单显示已支付但没有卡密？", a: "可能是库存补充中。订单页会显示「备货中」，客服会尽快处理；也可联系在线客服优先跟进。" },
    ],
  },
  {
    id: "redeem",
    number: "02",
    title: "MOLI 激活码核销",
    desc: "验证激活码并进入自助充能流程。",
    icon: "key",
    paragraphs: [
      "MOLI 激活码是商城发放的一次性核销凭证，格式为 MOLI-XXXX-XXXX-XXXX，仅可绑定一个目标账号。",
      "打开免密自助充能中心，输入激活码并验证；通过后按页面提示提交 Session，系统会自动创建任务并完成充值。",
    ],
    faqs: [
      { q: "激活码可以给别人用吗？", a: "激活码尚未绑定账号前可以转让，但一旦首次提交并绑定账号，便无法为其他账号使用。" },
      { q: "提示激活码已核销怎么办？", a: "说明该卡密已完成充值任务。请到「我的订单」查看交付记录；如有疑问，凭订单号联系客服核实。" },
    ],
  },
  {
    id: "session",
    number: "03",
    title: "Session JSON 使用指引",
    desc: "查看 ChatGPT / Claude 账户会话信息提交步骤。",
    icon: "json",
    paragraphs: [
      "ChatGPT：登录目标账号后访问 https://chatgpt.com/api/auth/session，复制整页 JSON（以 { 开头），粘贴到充能中心即可。",
      "Claude：登录 claude.ai 后打开开发者工具，在 Application / Cookies 中找到 sessionKey，复制其值（以 sk-ant- 开头）并粘贴提交。",
      "Session 仅用于本次订阅升级，系统会加密保存并在任务结束后 5 分钟内清除。请勿向任何人提供账号密码。",
    ],
    faqs: [
      { q: "Session 过期了怎么办？", a: "重新登录对应网站并重新复制最新的 Session 即可。过期的凭证会被系统直接拦截，不会消耗激活码。" },
      { q: "提示账号已是会员？", a: "系统预检发现账号已处于 Plus/Pro 状态时会阻止重复充值，激活码不会被消耗。" },
    ],
  },
  {
    id: "recharge",
    number: "04",
    title: "常见充值问题",
    desc: "处理失败、超时与状态异常。",
    icon: "alert",
    paragraphs: [
      "充值任务会经历「验证账号 → 创建任务 → 提交支付 → 等待确认 → 完成」五个阶段，页面每 2 秒自动刷新一次状态。",
      "因上游网络波动、临时超时等可恢复原因导致的失败，系统会自动释放激活码，可以直接在充能中心重试，不会重复扣卡。",
    ],
    faqs: [
      { q: "充值失败会扣卡密吗？", a: "可恢复失败会自动释放激活码，可安全重试。若任务进入人工复核状态，请联系客服并提供任务编号。" },
      { q: "充值后会员没有立即生效？", a: "通常在 1–2 分钟内生效。请刷新 ChatGPT / Claude 页面；如仍未生效，保留任务编号联系客服。" },
    ],
  },
  {
    id: "delivery",
    number: "05",
    title: "自动发货说明",
    desc: "了解成品号与激活码的交付方式。",
    icon: "package",
    paragraphs: [
      "激活码类商品：支付后自动发放 MOLI 激活码，需前往免密自助充能中心完成核销。",
      "成品号类商品：支付后自动发放完整账密（邮箱、密码、辅助邮箱与质保日期），可在订单详情复制全部内容。",
      "所有交付内容均展示在订单详情中，并以加密形式存储，仅用于订单账户与售后核验。",
    ],
    faqs: [
      { q: "成品号首次登录要注意什么？", a: "建议首次登录后修改密码，并妥善保存辅助邮箱信息。不要共享账号或频繁切换地区。" },
      { q: "可以改邮箱吗？", a: "成品号不支持更换主邮箱，请保留交付邮箱信息用于后续登录与售后。" },
    ],
  },
  {
    id: "warranty",
    number: "06",
    title: "售后与质保政策",
    desc: "按订单商品类型查看对应服务保障。",
    icon: "shield",
    paragraphs: [
      "售后范围以商品页标注为准。成品号类商品自交付起 30 天内，如出现掉订阅或封号且非买家原因，可申请补发或按未使用天数折算退款；代充类商品保障充值到账，封号不在质保范围。",
      "以下情况不在售后范围：买家自行修改密码、2FA、换绑邮箱或辅助邮箱、共享账号、频繁切换地区/网络；官方服务器容量波动、模型暂不可用、地区限制、手机验证等非本站交付问题；违反服务商条款或法律法规导致的限制。",
      "售后请通过页面在线客服提交订单编号、问题描述与截图。核实后按剩余天数折算：例如 30 天质保、已使用 8 天，剩余价值 = 订单金额 × (30-8)/30（支付通道已产生的费用不退，具体以订单页/商品页为准）。",
    ],
    faqs: [
      { q: "成品号和代充有什么区别？", a: "成品号是已开通会员的独立账号，交付账密；代充是在你已有的账号上升级会员。两者质保范围不同，请以下单页商品说明为准。" },
      { q: "修改密码或 2FA 会影响质保吗？", a: "如因自行修改密码、2FA、换绑邮箱/辅助邮箱或共享账号导致无法登录、掉订阅或封号，不在质保范围。需要修改安全设置时请先联系客服确认。" },
      { q: "官方模型暂时不可用算售后吗？", a: "官方服务器容量波动、模型暂时不可用、地区限制或手机验证等非本站交付问题，不属于售后范围；可参考客服指引稍后重试。" },
      { q: "退款金额如何计算？", a: "按未使用天数折算，例如 30 天质保、使用 8 天，剩余价值 = 订单金额 × (30-8)/30；支付通道已产生的费用不退。" },
    ],
  },
];

const POLICIES = [
  {
    id: "terms",
    title: "服务条款",
    paragraphs: [
      "MOLIPLY（模力补给）提供正版软件订阅代付与数字商品自动交付服务。下单即表示你已阅读并同意本条款及对应商品的说明。",
      "用户应确保在合法合规的网络环境下使用所购买的数字服务，并遵守对应服务商的使用条款。因用户自身违规使用导致的账号限制、封禁或权益损失，由用户自行承担。",
      "商品价格、权益与交付时效以商品页展示为准。数字商品具有可复制性，除质量问题外，交付后不支持无理由退款。",
    ],
  },
  {
    id: "privacy",
    title: "隐私政策",
    paragraphs: [
      "我们仅收集完成订单交付所必需的信息：接收邮箱、订单记录、交付凭证与必要的客户端 IP 地址。订单密码使用加盐哈希存储，我们不会保存或向第三方披露明文密码。",
      "用户提交的 Session / Session Key 仅用于完成本次订阅升级，使用 AES-256-GCM 加密存储，并在任务结束后 5 分钟内清除。",
      "我们不会向第三方出售用户信息；除支付通道、上游服务商与法律法规要求外，不会对外披露订单信息。",
    ],
  },
  {
    id: "warranty",
    title: "售后与质保",
    paragraphs: [
      "本政策适用于 MOLIPLY 商城销售的数字权益。质保期、售后范围以对应商品页和订单记录为准；商品页标注优先于本页通用说明。",
      "成品号类：交付后 30 天内，非买家原因出现掉订阅或封号，可按未使用天数补发或折算退款；代充类：保障充值到账，因卡源问题掉订阅按剩余天数处理，封号不保。",
      "不在售后范围：自行修改密码/2FA/换绑邮箱、共享账号、频繁切换地区或网络；官方服务器容量波动、模型暂不可用、地区限制、手机验证等非本站交付问题；违反服务商条款或法律法规导致的限制。",
      "申请售后请提供订单编号、问题描述与截图，通过页面在线客服提交。折算示例：30 天质保、使用 8 天，剩余价值 = 订单金额 × (30-8)/30；支付通道已产生的费用不退。",
    ],
  },
];

export default function HelpPage() {
  const [query, setQuery] = useState("");
  const [openTopic, setOpenTopic] = useState<string | null>("orders");
  const [openPolicy, setOpenPolicy] = useState<string | null>(null);
  const [contactOpen, setContactOpen] = useState(false);
  const [contactWechat, setContactWechat] = useState(BRAND.contactWechat);
  const [supportHours, setSupportHours] = useState("每日 09:00–24:00");

  useEffect(() => {
    api<BrandInfo>("/api/v1/brand")
      .then((data) => {
        if (data.support_wechat) setContactWechat(data.support_wechat);
        if (data.support_hours) setSupportHours(data.support_hours);
      })
      .catch(() => undefined);
  }, []);

  const filteredTopics = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return TOPICS;
    return TOPICS.filter((topic) => {
      const haystack = [topic.title, topic.desc, ...topic.paragraphs, ...topic.faqs.map((item) => `${item.q} ${item.a}`)]
        .join(" ")
        .toLowerCase();
      return haystack.includes(keyword);
    });
  }, [query]);

  function scrollToTopic(id: string) {
    setOpenTopic(id);
    window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  }

  return (
    <div className="relative">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[520px] overflow-hidden">
        <div className="absolute left-1/2 top-[-80px] h-[440px] w-[1000px] -translate-x-1/2 rounded-full bg-brand-600/[0.13] blur-[120px]" />
        <div className="grid-lines absolute inset-0 opacity-30" />
      </div>

      <div className="container-page relative pb-16 pt-14">
        <div className="mx-auto max-w-2xl text-center">
          <p className="eyebrow justify-center">MOLIPLY SUPPORT CENTER</p>
          <h1 className="mt-4 font-display text-[36px] font-bold tracking-tight text-white sm:text-[48px]">
            有什么可以帮你？
          </h1>
          <p className="mt-4 text-sm text-slate-500">订单、交付、核销、售后 — 快速找到需要的答案。</p>
          <div className="relative mx-auto mt-7 max-w-xl">
            <Search size={17} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索使用教程、订单问题或售后政策"
              className="field py-4 pl-12 pr-12"
              aria-label="搜索帮助内容"
            />
            <span className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2 rounded border border-white/10 px-1.5 py-0.5 text-[9px] text-slate-600">
              ↵
            </span>
          </div>
          {query && (
            <p className="mt-3 text-[11px] text-slate-600">
              {filteredTopics.length > 0 ? `找到 ${filteredTopics.length} 个相关主题` : "没有找到相关主题，试试其他关键词"}
            </p>
          )}
        </div>

        <div className="mt-10 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filteredTopics.map((topic) => {
            const Icon = ICONS[topic.icon];
            return (
              <button
                key={topic.id}
                type="button"
                onClick={() => scrollToTopic(topic.id)}
                className="surface surface-hover group p-5 text-left"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] text-slate-600">{topic.number}</span>
                  <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-brand-400/15 bg-brand-500/[0.07] text-brand-300">
                    <Icon size={16} />
                  </span>
                </div>
                <h2 className="mt-4 text-sm font-semibold text-slate-100">{topic.title}</h2>
                <p className="mt-2 text-[11px] leading-5 text-slate-500">{topic.desc}</p>
                <span className="mt-4 inline-flex items-center gap-1 text-[10px] text-slate-600 transition group-hover:text-brand-300">
                  查看指南
                  <ArrowRight size={11} />
                </span>
              </button>
            );
          })}
        </div>

        {filteredTopics.length === 0 && (
          <div className="mt-10 flex flex-col items-center rounded-2xl border border-white/[0.06] bg-white/[0.02] px-6 py-14 text-center">
            <Search size={24} className="text-slate-600" />
            <p className="mt-4 text-sm text-slate-400">没有找到匹配的内容</p>
            <p className="mt-1.5 text-xs text-slate-600">可以换个关键词，或直接联系在线客服。</p>
            <button type="button" onClick={() => setContactOpen(true)} className="btn-soft mt-5">联系客服</button>
          </div>
        )}

        <div className="mt-12 grid gap-5 lg:grid-cols-[1.55fr_0.85fr]">
          <div className="space-y-3">
            <h2 className="text-lg font-semibold text-slate-100">详细说明</h2>
            {filteredTopics.map((topic) => (
              <div key={topic.id} id={topic.id} className="scroll-mt-28 overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
                <button
                  type="button"
                  onClick={() => setOpenTopic(openTopic === topic.id ? null : topic.id)}
                  className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
                >
                  <span className="flex items-center gap-3">
                    <span className="font-mono text-[10px] text-slate-600">{topic.number}</span>
                    <span className="text-sm font-medium text-slate-200">{topic.title}</span>
                  </span>
                  <ChevronDown
                    size={16}
                    className={`shrink-0 text-slate-500 transition ${openTopic === topic.id ? "rotate-180" : ""}`}
                  />
                </button>
                {openTopic === topic.id && (
                  <div className="border-t border-white/[0.05] px-5 py-5">
                    <div className="space-y-3 text-[11px] leading-6 text-slate-500">
                      {topic.paragraphs.map((paragraph) => (
                        <p key={paragraph}>{paragraph}</p>
                      ))}
                    </div>
                    {topic.faqs.length > 0 && (
                      <div className="mt-5 space-y-2">
                        {topic.faqs.map((faq) => (
                          <div key={faq.q} className="rounded-xl border border-white/[0.05] bg-white/[0.02] p-3.5">
                            <p className="text-[11px] font-medium text-slate-300">{faq.q}</p>
                            <p className="mt-1.5 text-[10px] leading-5 text-slate-500">{faq.a}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            <div className="pt-4">
              <h2 className="text-lg font-semibold text-slate-100">服务条款与政策</h2>
              <div className="mt-3 space-y-2">
                {POLICIES.map((policy) => (
                  <div
                    key={`${policy.id}-${policy.title}`}
                    id={policy.id === "warranty" ? "help-warranty" : policy.id}
                    className="scroll-mt-28 overflow-hidden rounded-xl border border-white/[0.06] bg-white/[0.015]"
                  >
                    <button
                      type="button"
                      onClick={() => setOpenPolicy(openPolicy === policy.title ? null : policy.title)}
                      className="flex w-full items-center justify-between gap-4 px-4 py-3.5 text-left"
                    >
                      <span className="text-xs text-slate-300">{policy.title}</span>
                      <ChevronDown
                        size={14}
                        className={`text-slate-500 transition ${openPolicy === policy.title ? "rotate-180" : ""}`}
                      />
                    </button>
                    {openPolicy === policy.title && (
                      <div className="space-y-3 border-t border-white/[0.05] px-4 py-4 text-[10px] leading-5 text-slate-500">
                        {policy.paragraphs.map((paragraph) => (
                          <p key={paragraph}>{paragraph}</p>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>

          <aside className="space-y-4">
            <div id="contact" className="relative scroll-mt-28 overflow-hidden rounded-2xl border border-brand-400/20 bg-gradient-to-br from-[#0c1d3c] to-[#08111f] p-6">
              <div className="pointer-events-none absolute -right-10 -top-10 h-32 w-32 rounded-full bg-brand-500/20 blur-3xl" />
              <MessageCircle size={26} className="relative text-brand-300" />
              <h2 className="relative mt-4 text-lg font-semibold text-white">没有找到答案？</h2>
              <p className="relative mt-2 text-[11px] leading-6 text-slate-500">
                联系 MOLIPLY 客服，我们将协助处理订单问题。客服在线时间：{supportHours}。
              </p>
              <button type="button" onClick={() => setContactOpen(true)} className="btn-primary relative mt-5 w-full">
                联系在线客服
                <ArrowUpRight size={14} />
              </button>
            </div>

            <div className="surface p-5">
              <p className="text-xs font-medium text-slate-300">常见入口</p>
              <div className="mt-3 grid gap-2 text-[11px]">
                <Link href="/orders" className="flex items-center justify-between text-slate-500 hover:text-brand-300">
                  我的订单 <ArrowRight size={12} />
                </Link>
                <Link href="/redeem" className="flex items-center justify-between text-slate-500 hover:text-brand-300">
                  自助充能中心 <ArrowRight size={12} />
                </Link>
                <Link href="/products" className="flex items-center justify-between text-slate-500 hover:text-brand-300">
                  浏览商品 <ArrowRight size={12} />
                </Link>
              </div>
            </div>

            <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
              <p className="text-xs font-medium text-slate-300">我们不会索要</p>
              <ul className="mt-3 space-y-2 text-[10px] text-slate-500">
                <li>· 账号登录密码</li>
                <li>· 短信验证码或支付密码</li>
                <li>· 银行卡完整卡号与 CVV</li>
              </ul>
              <p className="mt-3 text-[10px] leading-5 text-slate-600">如遇索要以上信息的“客服”，请立即停止沟通并联系我们核实。</p>
            </div>
          </aside>
        </div>
      </div>

      <Modal open={contactOpen} onClose={() => setContactOpen(false)} title="联系 MOLIPLY 客服">
        <div className="space-y-4">
          <div className="rounded-xl border border-white/[0.07] bg-white/[0.025] p-4">
            <p className="text-[10px] uppercase tracking-[0.16em] text-slate-600">企业微信 / 客服微信</p>
            <div className="mt-2 flex items-center gap-3">
              <code className="flex-1 font-mono text-sm text-slate-200">{contactWechat}</code>
              <CopyButton value={contactWechat} label="复制" />
            </div>
          </div>
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 text-[11px] leading-6 text-slate-500">
            添加时请备注「订单咨询」，并准备好订单编号或 MOLI 激活码，方便客服快速定位。
            服务时间：{supportHours}；自助充能中心 24 小时可用。
          </div>
          <div className="flex justify-end">
            <button type="button" onClick={() => setContactOpen(false)} className="btn-secondary">知道了</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
