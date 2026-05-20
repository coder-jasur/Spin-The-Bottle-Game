/**
 * Telegram Mini App: avtomatik ro'yxatdan o'tish / kirish (/api/auth/telegram).
 */
(function (global) {
    "use strict";

    try {
        var legacyGm = global.localStorage.getItem("gm_coin");
        if (legacyGm !== null && global.localStorage.getItem("stars_coin") === null) {
            global.localStorage.setItem("stars_coin", legacyGm);
        }
        global.localStorage.removeItem("gm_coin");
    } catch (e) { /* ignore */ }

    var SUPPORTED_LANGS = { uz: 1, ru: 1, en: 1, tr: 1, az: 1, tj: 1, kz: 1 };
    var LANG_ALIASES = { kk: "kz", tg: "tj", kaz: "kz", tjk: "tj" };
    var DEFAULT_LANG = "ru";
    var TG_WAIT_MS = 1200;
    var START_PARAM_WAIT_MS = 3000;
    var FETCH_TIMEOUT_MS = 10000;
    var TELEGRAM_AUTH_TIMEOUT_MS = 45000;
    var TG_AUTH_MAX_RETRIES = 3;

    function getStartParam() {
        try {
            var tg = global.Telegram && global.Telegram.WebApp;
            if (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) {
                var sp = String(tg.initDataUnsafe.start_param).trim();
                if (sp) return sp;
            }
        } catch (e) { /* ignore */ }
        try {
            var up = new URLSearchParams(location.search || "");
            var q = up.get("ref") || up.get("startapp");
            if (q) return String(q).trim();
        } catch (e) { /* ignore */ }
        return null;
    }

    /** Taklif kodini localStorage ga saqlash (birinchi ochilishda start_param kechikishi mumkin). */
    function captureReferralParam() {
        var ref = getStartParam();
        if (ref) {
            try {
                global.localStorage.setItem("referral_ref", ref);
            } catch (e) { /* ignore */ }
            return ref;
        }
        try {
            return global.localStorage.getItem("referral_ref") || null;
        } catch (e) {
            return null;
        }
    }

    function waitForStartParam(maxMs, stepMs) {
        var limit = maxMs != null ? maxMs : START_PARAM_WAIT_MS;
        var step = stepMs || 50;
        return new Promise(function (resolve) {
            var elapsed = 0;
            function tick() {
                var ref = captureReferralParam();
                if (ref) return resolve(ref);
                if (elapsed >= limit) return resolve(captureReferralParam());
                elapsed += step;
                setTimeout(tick, step);
            }
            tick();
        });
    }

    function isRetryableNetworkError(err) {
        if (!err) return false;
        if (err.code === "TIMEOUT" || err.name === "AbortError") return true;
        var msg = String(err.message || err).toLowerCase();
        return (
            msg.indexOf("failed to fetch") >= 0 ||
            msg.indexOf("network") >= 0 ||
            msg.indexOf("load failed") >= 0 ||
            msg.indexOf("connection") >= 0
        );
    }

    function normalizeLang(raw) {
        if (!raw) return DEFAULT_LANG;
        var code = String(raw).trim().toLowerCase().replace(/_/g, "-");
        var primary = code.split("-")[0];
        if (SUPPORTED_LANGS[primary]) return primary;
        if (LANG_ALIASES[primary] && SUPPORTED_LANGS[LANG_ALIASES[primary]]) {
            return LANG_ALIASES[primary];
        }
        return DEFAULT_LANG;
    }

    function setLangCookie(lang) {
        var secure = location.protocol === "https:" ? "; secure" : "";
        document.cookie =
            "language=" + encodeURIComponent(lang) + "; path=/; max-age=31536000; samesite=lax" + secure;
    }

    function setCookie(name, value, days) {
        var expires = "";
        if (days) {
            var d = new Date();
            d.setTime(d.getTime() + days * 24 * 60 * 60 * 1000);
            expires = "; expires=" + d.toUTCString();
        }
        var secure = location.protocol === "https:" ? "; secure" : "";
        document.cookie =
            name + "=" + encodeURIComponent(value || "") + expires + "; path=/; samesite=lax" + secure;
    }

    function getAccessToken() {
        try {
            var t = global.localStorage && global.localStorage.getItem("accessToken");
            if (t) return t;
        } catch (e) { /* ignore */ }
        var m = (document.cookie || "").match(/(?:^|;\s*)accessToken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : null;
    }

    function syncCookiesFromStorage() {
        var t = getAccessToken();
        if (t) {
            setCookie("device_user_ids", t, 365);
            setCookie("accessToken", t, 365);
        }
    }

    function authHeaders() {
        var h = { Accept: "application/json" };
        var t = getAccessToken();
        if (t) h.Authorization = "Bearer " + t;
        return h;
    }

    function fetchWithTimeout(url, options, timeoutMs) {
        var ms = timeoutMs || FETCH_TIMEOUT_MS;
        if (typeof AbortController === "undefined") {
            return fetch(url, options);
        }
        var ctrl = new AbortController();
        var timedOut = false;
        var timer = setTimeout(function () {
            timedOut = true;
            ctrl.abort();
        }, ms);
        var opts = Object.assign({}, options || {}, { signal: ctrl.signal });
        return fetch(url, opts)
            .catch(function (err) {
                if (timedOut && err && err.name === "AbortError") {
                    var te = new Error("Request timeout after " + ms + "ms");
                    te.code = "TIMEOUT";
                    throw te;
                }
                throw err;
            })
            .finally(function () {
                clearTimeout(timer);
            });
    }

    function uploadTelegramProfilePhoto(photoUrl, accessToken) {
        if (!photoUrl || !accessToken) {
            return Promise.resolve(false);
        }
        return fetchWithTimeout(photoUrl, {}, 6000)
            .then(function (res) {
                if (!res.ok) throw new Error("photo fetch " + res.status);
                return res.blob();
            })
            .then(function (blob) {
                if (!blob || blob.size < 400) return false;
                var mime = (blob.type || "").toLowerCase();
                if (mime.indexOf("svg") >= 0 || mime.indexOf("html") >= 0) return false;
                var ext = mime.indexOf("png") >= 0 ? "png" : "jpg";
                var fd = new FormData();
                fd.append("profile_picture", blob, "avatar." + ext);
                return fetchWithTimeout(
                    "/api/auth/profile",
                    {
                        method: "POST",
                        headers: { Authorization: "Bearer " + accessToken },
                        credentials: "include",
                        body: fd,
                    },
                    10000
                )
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        if (!data || !data.success) return false;
                        var url =
                            data.profile_picture ||
                            data.avatar_url ||
                            data.url ||
                            "";
                        if (url) {
                            try {
                                global.localStorage.setItem("profile_picture", url);
                            } catch (e) { /* ignore */ }
                        }
                        return !!url;
                    });
            })
            .catch(function () {
                return false;
            });
    }

    function needsClientAvatarUpload(data) {
        if (!data || !data.user) return true;
        var pp = data.user.profile_picture || "";
        if (!pp || pp.indexOf("no_img") >= 0) return true;
        if (pp.indexOf("/photos/user_") >= 0) return false;
        return true;
    }

    function hasAuthToken() {
        return !!getAccessToken();
    }

    function hasGameSessionInUrl() {
        return /[?&]user_id=/.test(location.search || "");
    }

    function isTelegramWebApp() {
        return !!(global.Telegram && global.Telegram.WebApp);
    }

    function getTelegramUser() {
        try {
            var tg = global.Telegram && global.Telegram.WebApp;
            if (!tg || !tg.initDataUnsafe) return null;
            var u = tg.initDataUnsafe.user;
            return u && u.id ? u : null;
        } catch (e) {
            return null;
        }
    }

    function waitForTelegramUser(maxMs, stepMs) {
        var limit = maxMs != null ? maxMs : TG_WAIT_MS;
        var step = stepMs || 50;
        return new Promise(function (resolve) {
            var u = getTelegramUser();
            if (u) return resolve(u);
            if (!isTelegramWebApp()) return resolve(null);
            var elapsed = 0;
            var timer = setInterval(function () {
                elapsed += step;
                u = getTelegramUser();
                if (u) {
                    clearInterval(timer);
                    resolve(u);
                } else if (elapsed >= limit) {
                    clearInterval(timer);
                    resolve(null);
                }
            }, step);
        });
    }

    function persistUserSession(data) {
        if (!data || !data.accessToken) return;
        try {
            global.localStorage.setItem("accessToken", data.accessToken);
            if (data.refreshToken) {
                global.localStorage.setItem("refreshToken", data.refreshToken);
            }
            var u = data.user;
            if (!u) return;
            global.localStorage.setItem("id", String(u.id));
            var gameLogin = u.game_username || ("user_" + u.id);
            var tgUsername = "";
            if (u.telegram_username) {
                tgUsername = String(u.telegram_username).replace(/^@/, "");
            } else {
                var rawUn = (u.username || "").trim().replace(/^@/, "");
                if (rawUn && rawUn.indexOf("user_") !== 0) {
                    tgUsername = rawUn;
                }
            }
            if (!tgUsername) {
                tgUsername = u.display_name || gameLogin;
            }
            var displayName = u.display_name || tgUsername || gameLogin;
            global.localStorage.setItem("username", tgUsername);
            global.localStorage.setItem("game_username", gameLogin);
            global.localStorage.setItem("display_name", displayName);
            if (u.login) global.localStorage.setItem("login", u.login);
            global.localStorage.setItem("gender", u.gender || "male");
            global.localStorage.setItem("stars_coin", String(u.gm_coin != null ? u.gm_coin : 0));
            global.localStorage.setItem("balance", String(u.stars != null ? u.stars : 0));
            global.localStorage.setItem("profile_picture", u.profile_picture || "/photos/no_img.png");
            if (u.is_admin !== undefined) {
                global.localStorage.setItem("is_admin", u.is_admin ? "true" : "false");
            }
            var lang = data.lang || (u && u.lang) || null;
            if (lang) {
                global.localStorage.setItem("lang", normalizeLang(lang));
            } else if (global.localStorage.getItem("lang") === null) {
                global.localStorage.setItem("lang", DEFAULT_LANG);
            }
            if (global.localStorage.getItem("isMusicEnabled") === null) {
                global.localStorage.setItem("isMusicEnabled", "true");
            }
            if (global.localStorage.getItem("isSfxEnabled") === null) {
                global.localStorage.setItem("isSfxEnabled", "true");
            }
        } catch (e) {
            console.warn("[TG_AUTH] localStorage:", e);
        }
    }

    function clearAuth() {
        try {
            global.localStorage.removeItem("accessToken");
            global.localStorage.removeItem("refreshToken");
        } catch (e) { /* ignore */ }
        var secure = location.protocol === "https:" ? "; secure" : "";
        document.cookie = "accessToken=; path=/; max-age=0" + secure;
        document.cookie = "device_user_ids=; path=/; max-age=0" + secure;
    }

    function fetchGameEntryUrl() {
        if (!hasAuthToken()) {
            return Promise.resolve(null);
        }
        syncCookiesFromStorage();
        return fetchWithTimeout(
            "/api/auth/game-entry",
            {
                method: "GET",
                credentials: "include",
                headers: authHeaders(),
            },
            5000
        )
            .then(function (r) {
                if (!r.ok) {
                    if (r.status === 401 || r.status === 403) {
                        clearAuth();
                    }
                    return null;
                }
                return r.json();
            })
            .then(function (data) {
                if (data && data.needs_auth) {
                    clearAuth();
                    return null;
                }
                if (data && data.success && data.redirectUrl) {
                    return data.redirectUrl;
                }
                if (data && !data.success) {
                    clearAuth();
                }
                return null;
            })
            .catch(function () {
                return null;
            });
    }

    function applyAuthResponse(data) {
        if (!data || !data.success || !data.accessToken) return null;
        setCookie("device_user_ids", data.accessToken, 365);
        setCookie("accessToken", data.accessToken, 365);
        if (data.refreshToken) setCookie("refreshToken", data.refreshToken, 365);
        persistUserSession(data);
        if (data.lang) {
            var lang = normalizeLang(data.lang);
            setLangCookie(lang);
            try { global.localStorage.setItem("lang", lang); } catch (e) { /* ignore */ }
        }
        return data.redirectUrl || null;
    }

    function telegramLogin(options, knownTgUser) {
        options = options || {};
        captureReferralParam();
        var waitP = knownTgUser
            ? Promise.resolve(knownTgUser)
            : waitForTelegramUser(TG_WAIT_MS, 50);
        return waitP.then(function (tgUser) {
            return waitForStartParam(START_PARAM_WAIT_MS, 50).then(function () {
                return _telegramLoginAfterReady(options, tgUser);
            });
        });
    }

    function _telegramLoginAfterReady(options, tgUser) {
        var tg = global.Telegram && global.Telegram.WebApp;
        if (!tg) {
            return Promise.resolve({ ok: false, redirectUrl: null });
        }

        try {
            tg.ready();
            tg.expand();
        } catch (e) { /* ignore */ }

        var initData = tg.initDataUnsafe || {};
        var user = tgUser || initData.user;
        if (!user || !user.id) {
            return Promise.resolve({ ok: false, redirectUrl: null });
        }

        var tgLang = normalizeLang(user.language_code);
        setLangCookie(tgLang);
        try { global.localStorage.setItem("lang", tgLang); } catch (e) { /* ignore */ }

        var authData = {
            tg_id: user.id,
            first_name: user.first_name || null,
            last_name: user.last_name || null,
            username: user.username || null,
            photo_url: user.photo_url || null,
            language_code: user.language_code || null,
            start_param: captureReferralParam() || initData.start_param || null,
        };

        return fetchWithTimeout(
            "/api/auth/telegram",
            {
                method: "POST",
                headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
                credentials: "include",
                body: JSON.stringify(authData),
            },
            TELEGRAM_AUTH_TIMEOUT_MS
        )
            .then(function (r) {
                if (!r.ok) {
                    return r.text().then(function (txt) {
                        console.error("[TG_AUTH] telegram HTTP", r.status, txt);
                        return null;
                    });
                }
                return r.json();
            })
            .then(function (data) {
                if (!data || !data.success) {
                    console.error("[TG_AUTH] Xato:", data);
                    return { ok: false, redirectUrl: null };
                }
                var redirectUrl = applyAuthResponse(data);
                if (data.is_new) {
                    console.log("[TG_AUTH] Yangi foydalanuvchi, id=", data.user_id);
                }
                if (
                    !options.deferAvatar &&
                    user.photo_url &&
                    needsClientAvatarUpload(data)
                ) {
                    uploadTelegramProfilePhoto(user.photo_url, data.accessToken);
                }
                return { ok: !!redirectUrl, redirectUrl: redirectUrl };
            })
            .catch(function (err) {
                if (err && err.code === "TIMEOUT") {
                    console.warn("[TG_AUTH] Vaqt tugadi (" + TELEGRAM_AUTH_TIMEOUT_MS + "ms): /api/auth/telegram");
                } else if (err && err.name === "AbortError") {
                    console.warn("[TG_AUTH] So'rov bekor qilindi");
                } else {
                    console.error("[TG_AUTH] Tarmoq:", err);
                }
                return { ok: false, redirectUrl: null, retryable: isRetryableNetworkError(err) };
            });
    }

    function telegramLoginWithRetry(options, knownTgUser, attempt) {
        attempt = attempt || 0;
        return telegramLogin(options, knownTgUser).then(function (res) {
            if (res && res.ok) return res;
            if (res && res.retryable && attempt < TG_AUTH_MAX_RETRIES) {
                var delay = 800 * (attempt + 1);
                console.warn("[TG_AUTH] Qayta urinilmoqda (" + (attempt + 1) + "/" + TG_AUTH_MAX_RETRIES + ")...");
                return new Promise(function (resolve) {
                    setTimeout(resolve, delay);
                }).then(function () {
                    return telegramLoginWithRetry(options, knownTgUser, attempt + 1);
                });
            }
            return res || { ok: false, redirectUrl: null };
        });
    }

    function run(options) {
        options = options || {};

        return waitForTelegramUser(TG_WAIT_MS, 50).then(function (tgUser) {
            var inTg = isTelegramWebApp();
            var sessionInUrl = hasGameSessionInUrl();

            if (options.forceTelegram || (inTg && tgUser && !sessionInUrl)) {
                return telegramLoginWithRetry(
                    Object.assign({ deferAvatar: true }, options),
                    tgUser
                );
            }

            if (hasAuthToken() && !options.forceTelegram) {
                return fetchGameEntryUrl().then(function (redirectUrl) {
                    if (redirectUrl) {
                        return { ok: true, redirectUrl: redirectUrl };
                    }
                    if (inTg && tgUser) {
                        return telegramLoginWithRetry(
                            Object.assign({ deferAvatar: true }, options),
                            tgUser
                        );
                    }
                    return { ok: false, redirectUrl: null };
                });
            }

            if (inTg && tgUser) {
                return telegramLoginWithRetry(
                    Object.assign({ deferAvatar: true }, options),
                    tgUser
                );
            }
            return Promise.resolve({ ok: false, redirectUrl: null });
        });
    }

    function goToGame(redirectUrl) {
        if (!redirectUrl) return false;
        var target = redirectUrl.charAt(0) === "/" ? location.origin + redirectUrl : redirectUrl;
        location.replace(target);
        return true;
    }

    captureReferralParam();

    global.TgAutoAuth = {
        run: run,
        hasAuthToken: hasAuthToken,
        hasGameSessionInUrl: hasGameSessionInUrl,
        getAccessToken: getAccessToken,
        persistUserSession: persistUserSession,
        normalizeLang: normalizeLang,
        fetchGameEntryUrl: fetchGameEntryUrl,
        goToGame: goToGame,
        telegramLogin: telegramLogin,
        captureReferralParam: captureReferralParam,
        clearAuth: clearAuth,
    };
})(window);
