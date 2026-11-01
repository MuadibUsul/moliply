!function () {
    const $SwitchCategory = $(`.switch-category`), $ItemList = $(`.item-list`), categoryId = getVar("CAT_ID");
    const esc = YS.esc;
    const firstCat = String($SwitchCategory.not('[data-id="0"]').first().data('id') || '0');
    const state = {cat: categoryId > 0 ? String(categoryId) : firstCat, q: '', only: false, sort: 'rec'};
    let ALL = [];

    function _Stock(item) {
        const stock = Number(item && item.stock) || 0;
        return stock <= 0 ? 0 : (stock < 5 ? 1 : 2);
    }

    function _StockText(s) {
        return [i18n('暂时缺货'), i18n('少量'), i18n('现货')][s];
    }

    function _Tags(item) {
        const tags = Array.isArray(item && item.tags) ? item.tags : [];
        return tags.map(t => String((t && t.text) || '').trim()).filter(Boolean).map(t => `<span class="ys-tagx">${esc(t)}</span>`).join('');
    }

    function _Filtered() {
        let r = ALL.filter(item => {
            if (state.cat === 'recommend') return item.recommend == 1;
            if (state.cat !== '0') return String(item.category_id) === state.cat;
            return true;
        });
        if (state.only) r = r.filter(item => _Stock(item) > 0);
        const q = state.q.trim().toLowerCase();
        if (q) r = r.filter(item => (String(item.name) + ' ' + (item.category ? item.category.name : '')).toLowerCase().includes(q));
        const idx = new Map(ALL.map((x, i) => [x.id, i]));
        const f = {
            rec: (a, b) => ((_Stock(b) > 0) - (_Stock(a) > 0)) || (idx.get(a.id) - idx.get(b.id)),
            sold: (a, b) => (Number(b.order_sold) || 0) - (Number(a.order_sold) || 0),
            asc: (a, b) => Number(a.price) - Number(b.price),
            desc: (a, b) => Number(b.price) - Number(a.price)
        }[state.sort];
        return r.slice().sort(f);
    }

    function _Render() {
        const data = _Filtered();
        if (!data.length) {
            $ItemList.html(`<div class="ys-empty">${state.q ? i18n('没有找到相关商品') : i18n('没有商品')}<br>${i18n('想要的商品没上架，可以直接联系客服。')}</div>`);
            return;
        }
        $ItemList.html(data.map(item => {
            const s = _Stock(item), title = YS.title(i18n(item.name));
            return `<a href="/item/${item.id}" class="ys-item${s ? '' : ' out'}" data-id="${item.id}">
              ${YS.card(item)}
              <div class="ys-info"><div class="t">${esc(title)}${_Tags(item)}</div>
                <span class="ys-price ys-num">${YS_MONEY.html(item.price)}</span>
                <span class="ys-st s${s}"><i></i>${_StockText(s)} · ${i18n('已售')} ${Number(item.order_sold) || 0}</span></div>
            </a>`;
        }).join(''));
    }

    //首屏：Claude / Gemini / Grok / ChatGPT 四张会员卡斜向叠放，悬停时展开
    function _Showcase() {
        const brands = ['claude', 'gemini', 'grok', 'gpt'];
        const cards = brands.map(b => {
            const list = ALL.filter(item => YS.parse(item).b === b);
            if (!list.length) return null;
            //Grok 用银色 Premium+ 卡，和前后两张黑卡拉开层次
            const want = b === 'grok' ? list.filter(item => YS.parse(item).lv === 2)
                : b === 'gpt' ? list.filter(item => { const p = YS.parse(item); return p.tier === 'Plus' && /直充|direct/i.test(item.source_name || item.name); })
                : [];
            const pool = want.length ? want : list;
            const hero = pool.slice().sort((x, y) => (((_Stock(y) > 0) - (_Stock(x) > 0))) || (YS.parse(y).lv - YS.parse(x).lv) || (((_Stock(y) > 0) - (_Stock(x) > 0))) || ((Number(y.order_sold) || 0) - (Number(x.order_sold) || 0)))[0];
            return hero;
        }).filter(Boolean);
        $('.ys-showcase').html(cards.map((item, i) => `<a class="ys-stack-card" style="--i:${i}" href="/cat/${item.category_id}" data-cat="${item.category_id}" aria-label="${esc(YS.title(i18n(item.name)))}">${YS.card(item)}</a>`).join(''));
    }

    function _SwitchCategory(id, link = false) {
        state.cat = String(id);
        $SwitchCategory.removeClass("is-primary");
        $(`.switch-category[data-id="${id}"]`).addClass("is-primary");
        if (link) history.pushState(null, '', id == 0 ? '/' : `/cat/${id}`);
        _Render();
    }

    YS_MONEY.ready.then(() => { _Render(); YS_MONEY.notes(); });

    trade.getCommodityList({
        done: data => {
            ALL = Array.isArray(data) ? data : [];
            _Showcase();
            _SwitchCategory(state.cat);
        }
    });

    $SwitchCategory.click(function () {
        if ($(this).hasClass("is-primary")) return;
        _SwitchCategory($(this).data("id"), true);
    });

    $('.item-search-input').on('input', function () {
        state.q = $(this).val();
        $('.item-search-input').not(this).val(state.q);
        if (state.q && state.cat !== '0') {
            state.cat = '0';
            $SwitchCategory.removeClass("is-primary");
            $(`.switch-category[data-id="0"]`).addClass("is-primary");
        }
        _Render();
    }).on('keypress', function (e) {
        if (e.which === 13) document.getElementById('ys-store').scrollIntoView({behavior: 'smooth'});
    });

    $('.ys-only-in').on('change', function () { state.only = this.checked; _Render(); });
    $('.ys-sort').on('change', function () { state.sort = this.value; _Render(); });
    $(document).on('click', '.ys-stack-card', function (e) {
        e.preventDefault();
        const id = $(this).data('cat');
        if (String(id) !== state.cat) _SwitchCategory(id, true);
        document.getElementById('ys-store').scrollIntoView({behavior: 'smooth'});
    });
    $('[data-ys-scroll]').on('click', function (e) { e.preventDefault(); document.getElementById('ys-store').scrollIntoView({behavior: 'smooth'}); });

    if (!matchMedia('(hover: none)').matches && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
        $(document).on('pointermove', '.ys-item, .ys-stack-card', function (e) {
            const c = this.querySelector('.mcard');
            if (c) YS.sheen(c, e.originalEvent);
        });
    }
}();
