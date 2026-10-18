@extends('layouts.app')

@php
    $brandEn = env('BRAND_NAME_EN', 'MOLIPLY');
    $brandCn = env('BRAND_NAME_CN', '模力补给');
    $brandSubtitle = env('BRAND_SUBTITLE', 'AI 会员 · 软件订阅 · 数字商品');
    $brandSlogan = env('BRAND_HERO_SLOGAN', '让优质数字服务，触手可及。');
    $brandPrefix = env('BRAND_CODE_PREFIX', 'MOLI');
@endphp

@section('content')
<section class="ml-hero">
    <div class="ml-container ml-hero-grid">
        <div>
            <p class="ml-eyebrow"><span></span> YOUR DIGITAL MEMBERSHIP HUB · 24/7</p>
            <h1 class="ml-hero-title">
                让优质数字服务，<br>
                <span class="ml-gradient">触手可及。</span>
            </h1>
            <p class="ml-hero-subtitle">{{ $brandSubtitle }}</p>
            <p class="ml-muted ml-hero-copy">全球数字订阅与生产力工具，一站式选购。从下单到交付，简单、透明、快捷。</p>
            <div class="ml-hero-actions">
                <a class="ml-btn ml-btn-primary" href="{{ url('/products') }}">立即选购 ↗</a>
                <a class="ml-btn ml-btn-ghost" href="{{ url('/redeem') }}">进入自助充能中心 →</a>
            </div>
            <div class="ml-trust">
                <span>✦ 24 小时自动发货</span>
                <span>◈ 安全便捷</span>
                <span>◎ 交付进度可查</span>
                <span>✧ 专业售后</span>
            </div>
        </div>
        <div class="ml-hero-art">
            <div class="ml-orbit"></div>
            <div class="ml-platform">
                <div class="ml-tile ml-tile-gpt">GPT</div>
                <div class="ml-tile ml-tile-claude">✳</div>
                <div class="ml-tile ml-tile-cursor">◇</div>
                <div class="ml-tile ml-tile-mj">∿</div>
                <div class="ml-platform-label">{{ $brandEn }}<small>DIGITAL SUPPLY</small></div>
            </div>
        </div>
    </div>
</section>

<section class="ml-section">
    <div class="ml-container">
        <div class="ml-section-head">
            <div>
                <p class="ml-eyebrow"><span></span> EXPLORE BY CATEGORY</p>
                <h2>探索数字服务</h2>
            </div>
            <a class="ml-link" href="{{ url('/products') }}">查看全部分类 ↗</a>
        </div>
        <div class="ml-category-grid">
            <a class="ml-card ml-category" href="{{ url('/products?category=AI_MEMBERSHIP') }}">
                <span class="ml-category-icon">✳</span><strong>AI 会员</strong><small>ChatGPT · Claude · Gemini</small>
            </a>
            <a class="ml-card ml-category" href="{{ url('/products?category=SOFTWARE') }}">
                <span class="ml-category-icon">▤</span><strong>软件订阅</strong><small>Cursor · Notion · Adobe</small>
            </a>
            <a class="ml-card ml-category" href="{{ url('/products?category=DIGITAL') }}">
                <span class="ml-category-icon">◇</span><strong>数字商品</strong><small>礼品卡 · 激活码 · 数字权益</small>
            </a>
            <a class="ml-card ml-category" href="{{ url('/redeem') }}">
                <span class="ml-category-icon">✦</span><strong>自助充能中心</strong><small>{{ $brandPrefix }} 激活码 · 自动核销</small>
            </a>
        </div>
    </div>
</section>

<section class="ml-section">
    <div class="ml-container">
        <div class="ml-section-head">
            <div>
                <p class="ml-eyebrow"><span></span> HANDPICKED FOR YOU</p>
                <h2>热门补给</h2>
            </div>
            <a class="ml-link" href="{{ url('/products') }}">浏览全部商品 ↗</a>
        </div>

        <div class="ml-product-grid">
            @forelse (($products ?? []) as $product)
                @php
                    $name = data_get($product, 'name', '数字商品');
                    $subtitle = data_get($product, 'subtitle', '24 小时自动交付');
                    $price = data_get($product, 'price', 0);
                    $id = data_get($product, 'id');
                    $slug = data_get($product, 'slug');
                    $detailUrl = $slug ? url('/products/' . $slug) : url('/buy/' . $id);
                @endphp
                <a class="ml-card ml-product" href="{{ $detailUrl }}">
                    <div class="ml-product-logo">M</div>
                    <div class="ml-product-body">
                        <strong>{{ $name }}</strong>
                        <small>{{ $subtitle }}</small>
                    </div>
                    <div class="ml-product-bottom">
                        <span class="ml-price">¥{{ number_format((float) $price, 2) }}</span>
                        <span class="ml-arrow">↗</span>
                    </div>
                </a>
            @empty
                <div class="ml-card ml-empty">
                    <p>商品正在补给中，请稍后刷新页面。</p>
                </div>
            @endforelse
        </div>
    </div>
</section>

<section class="ml-section">
    <div class="ml-container">
        <div class="ml-cta">
            <h2>让每一次续费，都更简单。</h2>
            <p class="ml-muted">24 小时自动交付，激活码免密核销。选好服务，剩下的交给 {{ $brandEn }}。</p>
            <div class="ml-hero-actions">
                <a class="ml-btn ml-btn-primary" href="{{ url('/products') }}">立即选购 ↗</a>
                <a class="ml-btn ml-btn-ghost" href="{{ url('/redeem') }}">我有激活码</a>
            </div>
        </div>
    </div>
</section>
@endsection
