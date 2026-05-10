import {T as v, E as O, a as g, D as P, b as x, C, S as A, c as M, V as S, M as E} from "./vendor-1776688741387-ChdSrS24.js";
const F = new class {
    patch() {
        this.patchFrameSkip(),
        this.patchEvents()
    }
    patchFrameSkip() {
        V(),
        D(),
        _(),
        j(),
        k(),
        T()
    }
    patchEvents() {
        W(),
        I()
    }
    get pixiChanged() {
        return c
    }
    set pixiChanged(t) {
        c = t
    }
}
;
let c = !1;
function y(t, e, o, n=Object.is, r=!0) {
    const i = `_${e}`;
    Object.defineProperty(t.prototype, e, {
        get: function() {
            const s = this[i];
            return s ?? o
        },
        set: function(s) {
            const l = this;
            c || (c = !n.call(this, l[e], s) && (this.worldVisible && this.worldAlpha > 0 && this.renderable || !r)),
            l[i] = s
        }
    })
}
function a(t, e, o=Object.is) {
    const n = Object.getOwnPropertyDescriptor(t.prototype, e);
    if (!n)
        throw new Error("Original descriptor for PIXI component was not found");
    Object.defineProperty(t.prototype, e, {
        get: n.get,
        set: function(r) {
            var i;
            const s = this[e];
            c || (c = !o.call(this, s, r) && this.worldVisible && this.worldAlpha > 0 && this.renderable),
            (i = n?.set) === null || i === void 0 || i.call(this, r)
        }
    })
}
function p(t, e, o= () => !1) {
    const n = t.prototype
      , r = n[e];
    n[e] = function(...i) {
        return c || (c = !o.apply(this, i) && this.worldVisible && this.worldAlpha > 0 && this.renderable),
        r.apply(this, i)
    }
}
function u(t, e) {
    return t.equals(e)
}
function V() {
    const t = P;
    y(t, "visible", !0, function(e, o) {
        var n;
        return !(!((n = this.parent) === null || n === void 0) && n.worldVisible) || this.worldAlpha === 0 || !this.renderable || e === o
    }, !1),
    y(t, "renderable", !0, function(e, o) {
        var n;
        return !(!((n = this.parent) === null || n === void 0) && n.renderable) || this.worldAlpha === 0 || !this.worldVisible || e === o
    }, !1),
    y(t, "alpha", 1, function(e, o) {
        var n;
        return ((n = this.parent) === null || n === void 0 ? void 0 : n.worldAlpha) === 0 || !this.renderable || !this.worldVisible || e === o
    }, !1),
    p(t, "setTransform", function(e, o, n, r, i, s, l, h, d) {
        return this.pivot.x === e && this.pivot.y === o && this.position.x === h && this.position.y === d && this.rotation === n && this.scale.x === (r || 1) && this.scale.y === (i || 1) && this.skew.x === s && this.skew.y === l
    }),
    a(t, "x"),
    a(t, "y"),
    a(t, "position", u),
    a(t, "scale", u),
    a(t, "pivot", u),
    a(t, "skew", u),
    a(t, "rotation"),
    a(t, "angle", (e, o) => e === o * x),
    a(t, "zIndex"),
    a(t, "mask", () => !1)
}
function D() {
    const t = v
      , e = "onChange"
      , o = t.prototype[e];
    t.prototype[e] = function() {
        return c || (c = !0),
        o.apply(this)
    }
    ;
    const n = "updateSkew"
      , r = t.prototype[n];
    t.prototype[n] = function() {
        return c || (c = !0),
        r.apply(this)
    }
    ;
    const i = "rotation"
      , s = Object.getOwnPropertyDescriptor(v.prototype, i);
    if (!s)
        throw new Error("Original descriptor for PIXI component was not found");
    Object.defineProperty(v.prototype, i, {
        get: s.get,
        set: function(l) {
            var h;
            const d = this[i];
            c || (c = d !== l),
            (h = s?.set) === null || h === void 0 || h.call(this, l)
        }
    })
}
function _() {
    const t = C;
    p(t, "addChild"),
    p(t, "addChildAt", function(e, o) {
        return o < 0 || o > this.children.length
    }),
    p(t, "swapChildren", function(e, o) {
        return e === o
    }),
    p(t, "setChildIndex", function(e, o) {
        return o < 0 || o >= this.children.length
    }),
    p(t, "removeChild"),
    p(t, "removeChildAt"),
    p(t, "removeChildren"),
    p(t, "sortChildren")
}
function j() {
    const t = A;
    p(t, "_onTextureUpdate"),
    p(t, "_onAnchorUpdate"),
    a(t, "roundPixels"),
    a(t, "anchor", u),
    a(t, "tint")
}
function k() {
    const t = M;
    a(t, "text", (e, o) => String(e ?? "") === o),
    a(t, "resolution"),
    a(t, "style", () => !1)
}
function T() {
    const t = S
      , e = t.prototype.computeWorldVertices;
    t.prototype.computeWorldVertices = function(r, i, s, l, h, d) {
        const m = n(r);
        e.call(this, r, i, s, l, h, d);
        const b = n(r);
        if (m !== -1 && m !== b) {
            const f = r.currentMesh;
            c || (c = f.worldVisible && f.worldAlpha > 0 && f.renderable)
        }
    }
    ;
    const o = Math.pow(10, 9) + 7;
    function n(r) {
        const i = r.getAttachment();
        if (i !== null && i instanceof E) {
            const l = r.currentMesh.vertices;
            let h = 0;
            for (let d = 0; d < l.length; d++)
                h = (h * 31 + Math.round(l[d] * 1e6)) % o;
            return h
        }
        return -1
    }
}
let w = !0;
function I() {
    const t = g.prototype
      , e = "onPointerOverOut"
      , o = t[e]
      , n = ["pointerover", "mouseover"]
      , r = ["pointerleave", "mouseout"];
    t[e] = function(i) {
        n.indexOf(i.type) > -1 && (w = !0),
        r.indexOf(i.type) > -1 && (w = !1),
        o.call(this, i)
    }
}
function W() {
    const t = O.prototype
      , e = "mapPointerMove"
      , o = t[e];
    t[e] = function(n) {
        w && o.call(this, n)
    }
}
export {F as P};
