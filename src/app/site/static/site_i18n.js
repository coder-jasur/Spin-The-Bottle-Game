/**
 * Sayt sahifalari (stars-support, banned) uchun til.
 * ?lang=uz | cookie language | Telegram WebApp | navigator
 */
(function (global) {
  "use strict";

  var DEFAULT_LANG = "ru";
  var SUPPORTED = { uz: 1, ru: 1, en: 1, tr: 1, az: 1, kz: 1, tj: 1 };
  var ALIASES = { kk: "kz", kaz: "kz", tg: "tj", tjk: "tj", aze: "az", tur: "tr", eng: "en", rus: "ru", uzb: "uz" };

  var STRINGS = {
    uz: {
      "stars.title": "Stars yetarli emas",
      "stars.desc": "Siz sayt orqali kirdingiz — avtomatik to'lov cheki faqat Telegram bot orqali ishlaydi.",
      "stars.shortfall": "Yetishmaydi: taxminan {n} Stars",
      "stars.badge": "+{n} ★ kerak",
      "stars.hint": "Balansni to'ldirish uchun qo'llab-quvvatlashga yozing. Operator Stars sotib olishda yordam beradi.",
      "stars.btn_support": "Support — @{user}",
      "stars.btn_back": "← Orqaga qaytish",
      "stars.page_title": "Stars to'ldirish — Support",
      "ban.title": "Kirish taqiqlangan",
      "ban.desc": "Hisobingiz o'yin qoidalarini buzgani uchun vaqtincha cheklangan.",
      "ban.status_label": "Holat",
      "ban.status_value": "Faol ban",
      "ban.expires_label": "Ochilish vaqti",
      "ban.expires_loading": "Yuklanmoqda...",
      "ban.expires_forever": "Umrbod",
      "ban.expires_unknown": "Noma'lum",
      "ban.btn_appeal": "Apellyatsiya berish",
      "ban.btn_home": "Bosh sahifaga qaytish",
      "ban.footer": "Agar bu xatolik deb o'ylasangiz, qo'llab-quvvatlash bilan bog'laning.",
      "ban.page_title": "Kirish taqiqlangan | Spin The Bottle"
    },
    ru: {
      "stars.title": "Недостаточно Stars",
      "stars.desc": "Вы вошли через сайт — автоматический чек оплаты работает только через Telegram-бота.",
      "stars.shortfall": "Не хватает: примерно {n} Stars",
      "stars.badge": "+{n} ★ нужно",
      "stars.hint": "Напишите в поддержку для пополнения баланса. Оператор поможет купить Stars.",
      "stars.btn_support": "Поддержка — @{user}",
      "stars.btn_back": "← Назад",
      "stars.page_title": "Пополнение Stars — Поддержка",
      "ban.title": "Доступ запрещён",
      "ban.desc": "Ваш аккаунт временно ограничен за нарушение правил игры.",
      "ban.status_label": "Статус",
      "ban.status_value": "Активный бан",
      "ban.expires_label": "Разблокировка",
      "ban.expires_loading": "Загрузка...",
      "ban.expires_forever": "Навсегда",
      "ban.expires_unknown": "Неизвестно",
      "ban.btn_appeal": "Подать апелляцию",
      "ban.btn_home": "На главную",
      "ban.footer": "Если считаете это ошибкой, свяжитесь с поддержкой.",
      "ban.page_title": "Доступ запрещён | Spin The Bottle"
    },
    en: {
      "stars.title": "Not enough Stars",
      "stars.desc": "You signed in via the website — automatic payment invoices work only through the Telegram bot.",
      "stars.shortfall": "Short by: about {n} Stars",
      "stars.badge": "+{n} ★ needed",
      "stars.hint": "Contact support to top up your balance. An operator will help you buy Stars.",
      "stars.btn_support": "Support — @{user}",
      "stars.btn_back": "← Go back",
      "stars.page_title": "Top up Stars — Support",
      "ban.title": "Access denied",
      "ban.desc": "Your account is temporarily restricted for breaking the game rules.",
      "ban.status_label": "Status",
      "ban.status_value": "Active ban",
      "ban.expires_label": "Unban time",
      "ban.expires_loading": "Loading...",
      "ban.expires_forever": "Permanent",
      "ban.expires_unknown": "Unknown",
      "ban.btn_appeal": "Submit appeal",
      "ban.btn_home": "Back to home",
      "ban.footer": "If you think this is a mistake, contact support.",
      "ban.page_title": "Access denied | Spin The Bottle"
    },
    tr: {
      "stars.title": "Yeterli Stars yok",
      "stars.desc": "Site üzerinden giriş yaptınız — otomatik ödeme faturası yalnızca Telegram botu ile çalışır.",
      "stars.shortfall": "Eksik: yaklaşık {n} Stars",
      "stars.badge": "+{n} ★ gerekli",
      "stars.hint": "Bakiyeyi yüklemek için destekle iletişime geçin. Operatör Stars satın almanıza yardımcı olur.",
      "stars.btn_support": "Destek — @{user}",
      "stars.btn_back": "← Geri dön",
      "stars.page_title": "Stars yükleme — Destek",
      "ban.title": "Giriş yasak",
      "ban.desc": "Hesabınız oyun kurallarını ihlal ettiği için geçici olarak kısıtlandı.",
      "ban.status_label": "Durum",
      "ban.status_value": "Aktif ban",
      "ban.expires_label": "Açılma zamanı",
      "ban.expires_loading": "Yükleniyor...",
      "ban.expires_forever": "Süresiz",
      "ban.expires_unknown": "Bilinmiyor",
      "ban.btn_appeal": "İtiraz gönder",
      "ban.btn_home": "Ana sayfaya dön",
      "ban.footer": "Bunun bir hata olduğunu düşünüyorsanız destekle iletişime geçin.",
      "ban.page_title": "Giriş yasak | Spin The Bottle"
    },
    az: {
      "stars.title": "Kifayət qədər Stars yoxdur",
      "stars.desc": "Sayt vasitəsilə daxil oldunuz — avtomatik ödəniş çeki yalnız Telegram botu ilə işləyir.",
      "stars.shortfall": "Çatışmır: təxminən {n} Stars",
      "stars.badge": "+{n} ★ lazımdır",
      "stars.hint": "Balansı artırmaq üçün dəstəyə yazın. Operator Stars almaqda kömək edəcək.",
      "stars.btn_support": "Dəstək — @{user}",
      "stars.btn_back": "← Geri qayıt",
      "stars.page_title": "Stars artırma — Dəstək",
      "ban.title": "Giriş qadağandır",
      "ban.desc": "Hesabınız oyun qaydalarını pozduğu üçün müvəqqəti məhdudlaşdırılıb.",
      "ban.status_label": "Vəziyyət",
      "ban.status_value": "Aktiv ban",
      "ban.expires_label": "Açılma vaxtı",
      "ban.expires_loading": "Yüklənir...",
      "ban.expires_forever": "Ömürlük",
      "ban.expires_unknown": "Naməlum",
      "ban.btn_appeal": "Apellyasiya göndər",
      "ban.btn_home": "Əsas səhifəyə qayıt",
      "ban.footer": "Bunun səhv olduğunu düşünürsünüzsə, dəstək ilə əlaqə saxlayın.",
      "ban.page_title": "Giriş qadağandır | Spin The Bottle"
    },
    kz: {
      "stars.title": "Stars жеткіліксіз",
      "stars.desc": "Сіз сайт арқылы кірдіңіз — автоматты төлем тек Telegram бот арқылы жұмыс істейді.",
      "stars.shortfall": "Жетіспейді: шамамен {n} Stars",
      "stars.badge": "+{n} ★ керек",
      "stars.hint": "Балансты толтыру үшін қолдауға жазыңыз. Оператор Stars сатып алуға көмектеседі.",
      "stars.btn_support": "Қолдау — @{user}",
      "stars.btn_back": "← Артқа",
      "stars.page_title": "Stars толтыру — Қолдау",
      "ban.title": "Кіру тыйым салынған",
      "ban.desc": "Аккаунтыңыз ойын ережелерін бұзғаны үшін уақытша шектелген.",
      "ban.status_label": "Күйі",
      "ban.status_value": "Белсенді бан",
      "ban.expires_label": "Ашылу уақыты",
      "ban.expires_loading": "Жүктелуде...",
      "ban.expires_forever": "Мәңгі",
      "ban.expires_unknown": "Белгісіз",
      "ban.btn_appeal": "Шағым беру",
      "ban.btn_home": "Басты бетке",
      "ban.footer": "Қате деп ойласаңыз, қолдаумен байланысыңыз.",
      "ban.page_title": "Кіру тыйым салынған | Spin The Bottle"
    },
    tj: {
      "stars.title": "Stars кофӣ нест",
      "stars.desc": "Шумо тавассути сайт ворид шудед — пардохти автоматӣ танҳо тавассути боти Telegram кор мекунад.",
      "stars.shortfall": "Кам аст: тақрибан {n} Stars",
      "stars.badge": "+{n} ★ лозим",
      "stars.hint": "Барои пур кардани баланс ба дастгирӣ нависед. Оператор дар хариди Stars кӯмак мекунад.",
      "stars.btn_support": "Дастгирӣ — @{user}",
      "stars.btn_back": "← Бозгашт",
      "stars.page_title": "Пур кардани Stars — Дастгирӣ",
      "ban.title": "Вуруд манъ аст",
      "ban.desc": "Ҳисоби шумо барои вайрон кардани қоидаҳои бозӣ муваққатан маҳдуд шудааст.",
      "ban.status_label": "Ҳолат",
      "ban.status_value": "Бани фаъол",
      "ban.expires_label": "Вақти кушодан",
      "ban.expires_loading": "Бор шуда истодааст...",
      "ban.expires_forever": "Ҳамеша",
      "ban.expires_unknown": "Номаълум",
      "ban.btn_appeal": "Шикоят фиристодан",
      "ban.btn_home": "Ба саҳифаи асосӣ",
      "ban.footer": "Агар ин хато бошад, ба дастгирӣ муроҷиат кунед.",
      "ban.page_title": "Вуруд манъ аст | Spin The Bottle"
    }
  };

  function normalizeLang(raw) {
    if (!raw) return null;
    var code = String(raw).trim().toLowerCase().replace(/_/g, "-");
    if (!code) return null;
    var primary = code.split("-")[0];
    if (SUPPORTED[primary]) return primary;
    if (ALIASES[primary] && SUPPORTED[ALIASES[primary]]) return ALIASES[primary];
    return null;
  }

  function readCookie(name) {
    var m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[1]) : null;
  }

  function persistLangCookie(lang) {
    if (!lang) return;
    var code = normalizeLang(lang) || lang;
    var secure = location.protocol === "https:" ? "; secure" : "";
    document.cookie =
      "language=" + encodeURIComponent(code) + "; path=/; max-age=31536000; samesite=lax" + secure;
    try {
      document.documentElement.lang = code;
    } catch (e) {}
  }

  function resolveSiteLang() {
    var params = new URLSearchParams(location.search);
    var fromUrl = normalizeLang(params.get("lang")) || normalizeLang(params.get("locale"));
    if (fromUrl) return fromUrl;
    var fromCookie = normalizeLang(readCookie("language"));
    if (fromCookie) return fromCookie;
    try {
      var tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
        var tgLang = normalizeLang(tg.initDataUnsafe.user.language_code);
        if (tgLang) return tgLang;
      }
    } catch (e) {}
    var nav = normalizeLang(navigator.language || navigator.userLanguage);
    if (nav) return nav;
    return DEFAULT_LANG;
  }

  function format(str, vars) {
    if (!vars) return str;
    return str.replace(/\{(\w+)\}/g, function (_, k) {
      return vars[k] !== undefined && vars[k] !== null ? String(vars[k]) : "";
    });
  }

  var currentLang = DEFAULT_LANG;

  function t(key, vars) {
    var pack = STRINGS[currentLang] || STRINGS[DEFAULT_LANG] || {};
    var fallback = (STRINGS[DEFAULT_LANG] || {})[key] || key;
    var template = pack[key] !== undefined ? pack[key] : fallback;
    return format(template, vars);
  }

  function _parseI18nVars(el) {
    var vars = {};
    var attrVars = el.getAttribute("data-i18n-vars");
    if (attrVars) {
      try { vars = JSON.parse(attrVars); } catch (e) {}
    }
    return vars;
  }

  function applySiteI18n(page) {
    currentLang = resolveSiteLang();
    persistLangCookie(currentLang);
    document.documentElement.lang = currentLang;
    var nodes = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var key = el.getAttribute("data-i18n");
      if (!key) continue;
      el.textContent = t(key, _parseI18nVars(el));
    }
    var htmlNodes = document.querySelectorAll("[data-i18n-html]");
    for (var h = 0; h < htmlNodes.length; h++) {
      var hel = htmlNodes[h];
      var hkey = hel.getAttribute("data-i18n-html");
      if (!hkey) continue;
      hel.innerHTML = t(hkey, _parseI18nVars(hel));
    }
    var phNodes = document.querySelectorAll("[data-i18n-placeholder]");
    for (var p = 0; p < phNodes.length; p++) {
      var pel = phNodes[p];
      var pkey = pel.getAttribute("data-i18n-placeholder");
      if (!pkey) continue;
      pel.placeholder = t(pkey, _parseI18nVars(pel));
    }
    var titleKey =
      page === "ban"
        ? "ban.page_title"
        : page === "admin"
          ? "admin.page_title"
          : "stars.page_title";
    document.title = t(titleKey);
    return {
      lang: currentLang,
      t: t,
      refresh: function () {
        return applySiteI18n(page);
      },
    };
  }

  function registerPack(lang, dict) {
    var code = normalizeLang(lang) || lang;
    if (!code || !dict) return;
    if (!STRINGS[code]) STRINGS[code] = {};
    for (var k in dict) {
      if (Object.prototype.hasOwnProperty.call(dict, k)) STRINGS[code][k] = dict[k];
    }
  }

  function appendLangToUrl(url, lang) {
    try {
      var u = typeof url === "string" ? new URL(url, location.origin) : url;
      var code = normalizeLang(lang) || normalizeLang(readCookie("language"));
      if (code && !u.searchParams.has("lang")) u.searchParams.set("lang", code);
      return u.toString();
    } catch (e) {
      return url;
    }
  }

  global.SiteI18n = {
    resolveSiteLang: resolveSiteLang,
    applySiteI18n: applySiteI18n,
    registerPack: registerPack,
    persistLangCookie: persistLangCookie,
    appendLangToUrl: appendLangToUrl,
    readLangCookie: function () {
      return normalizeLang(readCookie("language"));
    },
    t: function (key, vars) {
      if (!currentLang) currentLang = resolveSiteLang();
      return t(key, vars);
    },
    normalizeLang: normalizeLang
  };
})(typeof window !== "undefined" ? window : this);
