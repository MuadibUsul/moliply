<?php

declare(strict_types=1);

/**
 * MOLIPLY · 蓝兔支付（Lanttu Pay）适配器
 *
 * 框架无关实现，便于接入 Dujiaoka / Dujiaoka NEXT 或自建商城。
 * 核心安全要求：
 *   1. 支付商品名统一从合规池生成，禁止透传站内商品名；
 *   2. 回调必须验签 + 校验金额 + 校验支付成功状态；
 *   3. 签名算法如与官方文档版本不同，仅修改 sign()。
 */
class MoliplyLtzfGateway
{
    private string $mchId;
    private string $appId;
    private string $key;
    private string $apiBase;
    private string $notifyUrl;
    private string $channel;

    /** 支付通道展示用商品名池（禁止包含敏感词） */
    private const TITLE_POOL = [
        '模力补给-数字商品订单-%s',
        'MOLIPLY-软件订阅服务-%s',
        '模力补给-数字权益卡券-%s',
    ];

    private const FORBIDDEN_WORDS = [
        'chatgpt', 'claude', 'openai', '代充', '虚拟卡', '外币', 'vpn', '代理', '翻墙',
    ];

    public function __construct(string $channel = 'wechat')
    {
        $this->mchId = (string) getenv('LTZF_MCH_ID');
        $this->appId = (string) getenv('LTZF_APP_ID');
        $this->key = (string) getenv('LTZF_KEY');
        $this->apiBase = rtrim((string) (getenv('LTZF_API_BASE') ?: 'https://api.ltzf.cn'), '/');
        $this->notifyUrl = (string) getenv('LTZF_NOTIFY_URL');
        $this->channel = $channel === 'alipay' ? 'alipay' : 'wechat';

        if ($this->mchId === '' || $this->key === '') {
            throw new RuntimeException('蓝兔支付商户参数未配置');
        }
    }

    /**
     * 构造发往微信/支付宝的合规商品名。
     */
    public function maskedTitle(string $orderNo): string
    {
        $suffix = substr($orderNo, -4) ?: '0000';
        $index = hexdec(substr(hash('sha256', $orderNo), 0, 2)) % count(self::TITLE_POOL);
        $title = sprintf(self::TITLE_POOL[$index], $suffix);
        $lower = strtolower($title);
        foreach (self::FORBIDDEN_WORDS as $word) {
            if (str_contains($lower, $word)) {
                return sprintf(self::TITLE_POOL[0], $suffix);
            }
        }
        return $title;
    }

    /**
     * 发起 Native 扫码支付。
     *
     * @return array{code_url:string, pay_url:string, trade_no:string}
     */
    public function createNative(string $orderNo, string $amount, string $clientIp = ''): array
    {
        $params = [
            'mch_id' => $this->mchId,
            'app_id' => $this->appId,
            'out_trade_no' => $orderNo,
            'total_fee' => number_format((float) $amount, 2, '.', ''),
            'body' => $this->maskedTitle($orderNo),
            'notify_url' => $this->notifyUrl,
            'timestamp' => (string) time(),
            'attach' => 'moliply',
        ];
        if ($clientIp !== '') {
            $params['client_ip'] = $clientIp;
        }
        $params['sign'] = $this->sign($params);

        $path = $this->channel === 'wechat' ? '/api/wxpay/native' : '/api/alipay/native';
        $payload = $this->post($this->apiBase . $path, $params);
        $data = $payload['data'] ?? [];
        $codeUrl = $data['code_url'] ?? $data['qr_code'] ?? $data['pay_url'] ?? '';
        if ($codeUrl === '') {
            throw new RuntimeException('蓝兔支付未返回二维码内容：' . ($payload['msg'] ?? 'unknown'));
        }
        return [
            'code_url' => (string) $codeUrl,
            'pay_url' => (string) ($data['pay_url'] ?? ''),
            'trade_no' => (string) ($data['trade_no'] ?? $data['order_no'] ?? ''),
        ];
    }

    /**
     * 校验异步回调。
     *
     * @param array<string,mixed> $payload
     * @return array{verified:bool, success:bool, order_no:string, amount:string, trade_no:string}
     */
    public function verifyNotify(array $payload): array
    {
        $received = strtoupper((string) ($payload['sign'] ?? ''));
        $expected = $this->sign($payload);
        $verified = $received !== '' && hash_equals($expected, $received);
        $status = strtolower((string) ($payload['trade_status'] ?? $payload['status'] ?? ''));
        return [
            'verified' => $verified,
            'success' => in_array($status, ['success', 'trade_success', '1', 'paid', 'completed'], true),
            'order_no' => (string) ($payload['out_trade_no'] ?? ''),
            'amount' => (string) ($payload['total_fee'] ?? $payload['amount'] ?? '0'),
            'trade_no' => (string) ($payload['transaction_id'] ?? $payload['trade_no'] ?? ''),
        ];
    }

    public function successResponse(): string
    {
        return 'SUCCESS';
    }

    /**
     * 蓝兔支付签名：参数按字典序拼接，追加 &key=，MD5 后大写。
     *
     * @param array<string,mixed> $params
     */
    public function sign(array $params): string
    {
        unset($params['sign']);
        ksort($params);
        $pairs = [];
        foreach ($params as $name => $value) {
            if ($value === null || $value === '') {
                continue;
            }
            $pairs[] = $name . '=' . $value;
        }
        return strtoupper(md5(implode('&', $pairs) . '&key=' . $this->key));
    }

    /**
     * @param array<string,mixed> $params
     * @return array<string,mixed>
     */
    private function post(string $url, array $params): array
    {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => http_build_query($params),
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 20,
            CURLOPT_HTTPHEADER => ['Content-Type: application/x-www-form-urlencoded'],
        ]);
        $response = curl_exec($ch);
        $error = curl_error($ch);
        curl_close($ch);
        if ($response === false) {
            throw new RuntimeException('蓝兔支付请求失败：' . $error);
        }
        $decoded = json_decode((string) $response, true);
        if (!is_array($decoded)) {
            throw new RuntimeException('蓝兔支付返回非 JSON 数据');
        }
        return $decoded;
    }
}
