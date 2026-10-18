@php
    $brandEn = env('BRAND_NAME_EN', 'MOLIPLY');
    $brandCn = env('BRAND_NAME_CN', '模力补给');
    $brandSubtitle = env('BRAND_SUBTITLE', 'AI 会员 · 软件订阅 · 数字商品');
    $brandSlogan = env('BRAND_HERO_SLOGAN', '让优质数字服务，触手可及。');
@endphp
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ $brandEn }} {{ $brandCn }} | {{ $brandSubtitle }}</title>
    <meta name="description" content="{{ $brandSlogan }} {{ $brandEn }}（{{ $brandCn }}）提供 24 小时自动交付的 AI 会员、软件订阅与数字商品服务。">
    <meta name="keywords" content="MOLIPLY, 模力补给, AI 会员, 软件订阅, 数字商品, 激活码">
    <link rel="stylesheet" href="{{ asset('assets/theme/moliply.css') }}">
    @stack('head')
</head>
<body>
<header class="ml-nav">
    <div class="ml-container ml-nav-inner">
        <a class="ml-logo" href="{{ url('/') }}">
            <span class="ml-logo-mark">M</span>
            <span class="ml-logo-text">
                <strong>{{ $brandEn }}</strong>
                <small>{{ $brandCn }}</small>
            </span>
        </a>
        <nav class="ml-nav-links">
            <a href="{{ url('/') }}">首页</a>
            <a href="{{ url('/products') }}">全部商品</a>
            <a href="{{ url('/products?category=AI_MEMBERSHIP') }}">AI 会员</a>
            <a href="{{ url('/products?category=SOFTWARE') }}">软件订阅</a>
            <a href="{{ url('/help') }}">帮助中心</a>
        </nav>
        <div class="ml-nav-actions">
            <a class="ml-btn ml-btn-ghost" href="{{ url('/orders') }}">订单查询</a>
            <a class="ml-btn ml-btn-primary" href="{{ url('/products') }}">立即选购</a>
        </div>
    </div>
</header>

<main>
    @yield('content')
</main>

<footer class="ml-footer">
    <div class="ml-container">
        <div class="ml-footer-grid">
            <div>
                <a class="ml-logo" href="{{ url('/') }}">
                    <span class="ml-logo-mark">M</span>
                    <span class="ml-logo-text"><strong>{{ $brandEn }}</strong><small>{{ $brandCn }}</small></span>
                </a>
                <p class="ml-muted ml-footer-copy">{{ $brandSlogan }}<br>{{ $brandSubtitle }} · 24 小时自动交付</p>
            </div>
            <div>
                <p class="ml-footer-title">快速导航</p>
                <a href="{{ url('/products') }}">全部商品</a>
                <a href="{{ url('/redeem') }}">自助充能中心</a>
                <a href="{{ url('/orders') }}">订单查询</a>
                <a href="{{ url('/help') }}">帮助中心</a>
            </div>
            <div>
                <p class="ml-footer-title">服务与支持</p>
                <a href="{{ url('/help#terms') }}">服务条款</a>
                <a href="{{ url('/help#privacy') }}">隐私政策</a>
                <a href="{{ url('/help#contact') }}">联系客服</a>
            </div>
        </div>
        <div class="ml-footer-bottom">
            <p class="ml-muted">
                {{ $brandEn }}（{{ $brandCn }}）仅提供正版软件订阅代付与数字商品自动交付服务，请确保在合法合规的网络环境下使用。
            </p>
            <p class="ml-muted">© {{ date('Y') }} {{ $brandEn }} · {{ $brandCn }}. All rights reserved.</p>
        </div>
    </div>
</footer>
</body>
</html>
