<?php

declare(strict_types=1);

/**
 * MOLIPLY · 虎皮椒支付 V3 适配器
 *
 * 框架无关实现。商品名脱敏、回调验签、金额校验与 Blueprint 一致。
 * 实际字段/签名规则请以签约商户后台的 V3 文档为准。
 */
class MoliplyXunhupayGateway
{
    private string $appId;
    private string $appSecret;
    private string $apiBase;
    private string $notifyUrl;
    private string $returnUrl;

    private const TITLE_POOL = [
        '模力补给-数字商品订单-%s',
        'MOLIPLY-软件订阅服务-%s',
        '模力补给-数字权益卡券-%s',
    ];

    private const FORBIDDEN_WORDS = [
        'chatgpt', 'claude', 'openai', '代充', '虚拟卡', '外币', 'vpn', '代理', '翻墙',
    ];

    public function __construct()
    {
        $this->appId = (string) getenv('XUNHUPAY_APP_ID');
        $this->appSecret = (string) getenv('XUNHUPAY_APP_SECRET');
        $this->apiBase = rtrim((string) (getenv('XUNHUPAY_API_BASE') ?: 'https://api.xunhupay.com'), '/');
        $this->notifyUrl = (string) getenv('XUNHUPAY_NOTIFY_URL');
        $this->returnUrl = (string) getenv('XUNHUPAY_RETURN_URL');

        if ($this->appId === '' || $this->appSecret === '') {
            throw new RuntimeException('虎皮椒支付参数未配置');
        }
    }

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
     * @return array{code_url:string, pay_url:string, trade_no:string}
     */
    public function createNative(string $orderNo, string $amount): array
    {
        $params = [
            'appid' => $this->appId,
            'trade_order_id' => $orderNo,
            'total_fee' => number_format((float) $amount, 2, '.', ''),
            'title' => $this->maskedTitle($orderNo),
            'time' => (string) time(),
            'notify_url' => $this->notifyUrl,
            'return_url' => $this->returnUrl,
            'nonce_str' => bin2hex(random_bytes(8)),
            'type' => 'WAP',
            'wap_url' => (string) (getenv('PUBLIC_STORE_URL') ?: ''),
            'wap_name' => 'MOLIPLY',
        ];
        $params['hash'] = $this->sign($params);

        $payload = $this->post($this->apiBase . '/payment/do.html', $params);
        if ((string) ($payload['errcode'] ?? '') !== '0') {
            throw new RuntimeException('虎皮椒下单失败：' . ($payload['errmsg'] ?? 'unknown'));
        }
        $url = (string) ($payload['url_qrcode'] ?? $payload['url'] ?? '');
        if ($url === '') {
            throw new RuntimeException('虎皮椒未返回二维码内容');
        }
        return [
            'code_url' => $url,
            'pay_url' => (string) ($payload['url'] ?? ''),
            'trade_no' => (string) ($payload['order_id'] ?? ''),
        ];
    }

    /**
     * @param array<string,mixed> $payload
     * @return array{verified:bool, success:bool, order_no:string, amount:string, trade_no:string}
     */
    public function verifyNotify(array $payload): array
    {
        $received = strtolower((string) ($payload['hash'] ?? ''));
        $expected = $this->sign($payload);
        $verified = $received !== '' && hash_equals($expected, $received);
        $status = strtolower((string) ($payload['status'] ?? ''));
        return [
            'verified' => $verified,
            'success' => in_array($status, ['od', 'success', 'paid', 'completed'], true),
            'order_no' => (string) ($payload['trade_order_id'] ?? ''),
            'amount' => (string) ($payload['total_fee'] ?? '0'),
            'trade_no' => (string) ($payload['transaction_id'] ?? $payload['open_order_id'] ?? ''),
        ];
    }

    public function successResponse(): string
    {
        return 'success';
    }

    /**
     * 虎皮椒 V3 签名：参数按键名排序后 urlencode，追加 appsecret，小写 MD5。
     *
     * @param array<string,mixed> $params
     */
    public function sign(array $params): string
    {
        unset($params['hash']);
        ksort($params);
        $pairs = [];
        foreach ($params as $name => $value) {
            if ($value === null || $value === '') {
                continue;
            }
            $pairs[] = $name . '=' . rawurlencode((string) $value);
        }
        return md5(implode('&', $pairs) . $this->appSecret);
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
        ]);
        $response = curl_exec($ch);
        $error = curl_error($ch);
        curl_close($ch);
        if ($response === false) {
            throw new RuntimeException('虎皮椒请求失败：' . $error);
        }
        $decoded = json_decode((string) $response, true);
        if (!is_array($decoded)) {
            throw new RuntimeException('虎皮椒返回非 JSON 数据');
        }
        return $decoded;
    }
}
