// Self-test of emotes.c without the game (tests/test_emote_build.py builds and runs it with Savage Wind's zig).
//
// It includes emotes.c and drives tick() against a made-up Link: a fake guest_call answers the game calls (play a
// set by name, what slot 0 plays, finished, the ragdoll switch) from a list of set names the made-up Link has; a
// made-up AI tree (RootAi at Link +0x558, PlayerRoot, PlayerNormal, its Wait / Move / slip-fall children), equipment and
// ragdoll manager live in one block (W). The memory check's window is set to that block only, as in the game: the
// DLL's own set names lie outside it. The fake play call points slot 0's name at the made-up game's own text of that set
// (as Link's holder keeps the animation list's name); with W.keep_ptr it keeps the caller's char* instead. Each frame
// the made-up AI runs PlayerWait's calc (through the DLL's wrapper) while PlayerWait is current all the way down, and
// that calc puts Wait back whenever slot 0's text isn't Wait (the game's standing code 0x7000ce0c44, the reason the
// Game check saw Wait at once); with W.revert Link's own update puts Wait back too. No game code, no game data.
//
// emotes_selftest(out, cap) writes one line per check ("<group> <check> PASS|FAIL <detail>") and returns the number
// of failed checks.
#include "../emotes.c"

#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wbuiltin-requires-header"
int vsnprintf(char *buf, size_t n, const char *fmt, __builtin_va_list ap);
#pragma clang diagnostic pop

static char *g_out;
static int g_cap, g_len, g_fails;
static void say(const char *fmt, ...) {
    if (!g_out || g_len >= g_cap) return;
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    int n = vsnprintf(g_out + g_len, (size_t)(g_cap - g_len), fmt, ap);
    __builtin_va_end(ap);
    if (n > 0) g_len = g_len + n < g_cap ? g_len + n : g_cap - 1;
}
static void check(const char *group, const char *name, int ok, const char *detail) {
    if (!ok) g_fails++;
    say("%s %s %s %s\n", group, name, ok ? "PASS" : "FAIL", detail ? detail : "");
}

// ------------------------------------------------------------------ a made-up Link
typedef struct {
    _Alignas(16) uint8_t link[0x2700];
    _Alignas(16) uint8_t holder[0x100];
    _Alignas(16) uint8_t rootai[0x40], root[0x40], normal[0x40], wait[0x40], move[0x40], slip[0x40], mgr[0x200];
    _Alignas(16) uint64_t rootai_kids[1], root_kids[1], normal_kids[3];
    _Alignas(16) uint8_t item[0x60];     // a link data block for an item in the equipment's slot 0
    _Alignas(16) uint8_t other[0x2700];  // a second Link (a load)
    int32_t damage;      // the slip-fall's own damage value
    char names[24][32];  // the made-up animation list's own texts (one stable pointer per set name)
    char wait_text[8];   // the game's own "Wait" in its code (the mask points here)
    struct {
        uint64_t vptr;
        const char *str;  // the playing set's name: the list's text, or the char* a caller passed
    } cur_obj;
    int finished;
    int broken;          // the play call does nothing at all
    int keep_ptr;        // the play call keeps the caller's char* (else: the list's text)
    int revert;          // the made-up Link's update puts Wait back each frame
    int no_pw;           // the made-up AI doesn't run PlayerWait's calc (the first try's world)
    int stand_replay;    // PlayerWait's calc puts Wait back when slot 0's text isn't Wait (the game: 1)
    const char *pw_plays;  // PlayerWait's calc plays this set of its own (cold, out of breath...)
    int pw_saw_wait;     // PlayerWait's calc read Wait while a set of ours played
    const char *lacks[4];  // set names the made-up Link doesn't have
    // the upper body (slot 1): sub 0 gets the body set from Link's animation step when his body play flagged it (the
    // game: Link +0xc48 bit 0x1000, his vtable +0x390); sub 1 holds an upper-body set (talks)
    int body_flag;
    struct {
        uint64_t vptr;
        const char *str;
    } body1_obj, upper_obj, none_obj;
    int upper_finished;
} World;
static World W;
static double g_now;
static float g_pos[3];
static int calls_play, calls_rd, calls_generic, calls_upper, calls_stop_upper;
static char last_play[32];
static uint64_t last_keep;
static float last_morph;
static uint64_t last_actor;  // x0 of the last body play (Link's own play takes Link)

static void w64(void *base, size_t off, uint64_t v) { memcpy((uint8_t *)base + off, &v, 8); }
static uint64_t r64(const void *base, size_t off) {
    uint64_t v;
    memcpy(&v, (const uint8_t *)base + off, 8);
    return v;
}
static void set_child(void *node, uint16_t i) { memcpy((uint8_t *)node + AI_CUR, &i, 2); }
static uint16_t child_of(const void *node) {
    uint16_t i;
    memcpy(&i, (const uint8_t *)node + AI_CUR, 2);
    return i;
}
// the made-up animation list's text of a set name (stable: one per name, as the game's list)
static const char *wname(const char *name) {
    for (int i = 0; i < 24; i++) {
        if (!W.names[i][0]) {
            snprintf(W.names[i], sizeof W.names[i], "%s", name);
            return W.names[i];
        }
        if (!strcmp(W.names[i], name)) return W.names[i];
    }
    return W.names[23];
}

// ai: 1 = the game's layout (Link +0x558 RootAi -> PlayerRoot -> PlayerNormal), 2 = PlayerRoot at Link +0x100 (the
// first try's scan), 0 = nothing the DLL can find (the made-up AI still runs)
static void world_init(int ai) {
    memset(&W, 0, sizeof W);
    w64(W.link, 0, PLAYER_VPTR);
    w64(W.link, ACTOR_AS, P(W.holder));
    w64(W.holder, AS_OWNER, P(W.link));
    w64(W.link, LINK_RAGDOLL_MGR, P(W.mgr));
    w64(W.rootai, 0, VT_ROOTAI);
    w64(W.root, 0, VT_ROOT);
    w64(W.normal, 0, VT_NORMAL);
    w64(W.wait, 0, VT_WAIT);
    w64(W.move, 0, VT_MOVE);
    w64(W.slip, 0, VT_SLIP);
    uint8_t *nodes[] = {W.rootai, W.root, W.normal, W.wait, W.move, W.slip};
    for (int i = 0; i < 6; i++) w64(nodes[i], AI_ACTOR, P(W.link));
    W.damage = 1;
    w64(W.slip, SLIP_DAMAGE, P(&W.damage));
    W.rootai_kids[0] = P(W.root);
    W.root_kids[0] = P(W.normal);
    W.normal_kids[0] = P(W.wait), W.normal_kids[1] = P(W.move), W.normal_kids[2] = P(W.slip);
    uint32_t one = 1, three = 3;
    memcpy(W.rootai + AI_NCHILD, &one, 4);
    w64(W.rootai, AI_CHILDREN, P(W.rootai_kids));
    memcpy(W.root + AI_NCHILD, &one, 4);
    w64(W.root, AI_CHILDREN, P(W.root_kids));
    memcpy(W.normal + AI_NCHILD, &three, 4);
    w64(W.normal, AI_CHILDREN, P(W.normal_kids));
    set_child(W.rootai, 0);
    set_child(W.root, 0);
    set_child(W.normal, 0);  // standing (PlayerWait)
    if (ai == 1) w64(W.link, LINK_ROOT_AI, P(W.rootai));
    if (ai == 2) w64(W.link, 0x100, P(W.root));
    W.cur_obj.str = wname("Wait");
    W.body1_obj.str = W.cur_obj.str;
    W.upper_obj.str = W.none_obj.str = "";
    memcpy(W.wait_text, "Wait", 5);
    W.stand_replay = 1;
    w64(W.link, ACTOR_EQUIP, EQUIP_VPTR);
    w64(W.link, ACTOR_EQUIP + EQUIP_OWNER, P(W.link));
    g_guest_lo = P(&W), g_guest_hi = P(&W) + sizeof W;  // the "game's memory": only the made-up game
    memset(g_mem, 0, sizeof g_mem);
    g_mask_name = P(W.wait_text);
    g_mask_mode = MASK_CALC;
}
// the made-up game plays one of its own sets (its list's text), with Link's own body play
static void game_plays(const char *name) {
    W.cur_obj.str = wname(name);
    W.finished = 0;
    W.body_flag = 1;
}
static const char *cur(void) { return W.cur_obj.str; }
// something in the equipment's slot 0: in Link's hand (back byte 0) or on his back
static void hold_weapon(int in_hand) {
    uint64_t l = ACTOR_EQUIP + EQUIP_LINKS;
    w64(W.link, l, P(W.item));
    uint32_t id = 5;
    memcpy(W.link + l + 8, &id, 4);
    memcpy(W.item + LINKDATA_ID, &id, 4);
    w64(W.item, LINKDATA_PROC, P(W.item));
    W.link[l + EQUIP_BACK] = in_hand ? 0 : 1;
}
static void drop_weapon(void) { w64(W.link, ACTOR_EQUIP + EQUIP_LINKS, 0); }
static int lacks(const char *name) {
    for (int i = 0; i < 4; i++)
        if (W.lacks[i] && !strcmp(W.lacks[i], name)) return 1;
    return 0;
}

// the fake game calls
static void fake_call(struct Cpu *c, uint64_t addr) {
    SwModRegs *r = SWMOD_REGS(c);
    if (addr == AS_PLAY || addr == LINK_PLAY_BODY) {
        // AS_PLAY (action, name, keep, slot, sub): slot 0 only. LINK_PLAY_BODY (Link, name, force): slot 0 and the
        // flag for the animation step (slot 1 sub 0 follows); keep = !force, the same name compare
        const char *name = (const char *)(uintptr_t)r->x[1];
        int body = addr == LINK_PLAY_BODY;
        int keep = body ? !(r->x[2] & 1) : r->x[2] != 0;
        calls_play++;
        if (!body) calls_generic++;
        snprintf(last_play, sizeof last_play, "%s", name);
        last_keep = (uint64_t)keep;
        last_morph = swmod_get_s(c, 0);
        last_actor = body ? r->x[0] : r64((const void *)(uintptr_t)r->x[0], 8);
        if (!W.broken && !lacks(name) && !(keep && !strcmp(W.cur_obj.str, name)) &&
            (!body || r->x[0] == P(W.link))) {
            W.cur_obj.str = W.keep_ptr ? name : wname(name);
            W.finished = 0;
            if (body) W.body_flag = 1;
        }
        r->x[0] = 0;
    } else if (addr == LINK_PLAY_UPPER) {
        const char *name = (const char *)(uintptr_t)r->x[1];
        calls_upper++;
        if (r->x[0] == P(W.link) && !lacks(name)) W.upper_obj.str = wname(name), W.upper_finished = 0;
        r->x[0] = 0;
    } else if (addr == LINK_STOP_UPPER) {
        calls_stop_upper++;
        if (r->x[0] == P(W.link)) W.upper_obj.str = "";
        r->x[0] = 0;
    } else if (addr == AS_CURRENT) {
        r->x[0] = r->x[1] == 0 && r->x[2] == 0 ? P(&W.cur_obj)
                  : r->x[1] == 1 && r->x[2] == 0 ? P(&W.body1_obj)
                  : r->x[1] == 1 && r->x[2] == 1 ? P(&W.upper_obj) : P(&W.none_obj);
    } else if (addr == AS_FINISHED) {
        r->x[0] = r->x[1] == 1 && r->x[2] == 1 ? (uint64_t)W.upper_finished : (uint64_t)W.finished;
    } else if (addr == RAGDOLL_ON) {
        calls_rd++;
        if (r->x[0] == P(W.normal)) set_child(W.normal, 2);
        r->x[0] = 0;
    }
}
static void fake_log(const char *fmt, ...) { (void)fmt; }
static char g_reply[2048];
static int g_reply_failed;
static void fake_reply_str(SwModReply *r, const char *k, const char *v) {
    (void)r;
    size_t n = strlen(g_reply);
    snprintf(g_reply + n, sizeof g_reply - n, "%s=%s;", k, v);
}
static void fake_reply_int(SwModReply *r, const char *k, int64_t v) {
    (void)r;
    size_t n = strlen(g_reply);
    snprintf(g_reply + n, sizeof g_reply - n, "%s=%lld;", k, (long long)v);
}
static void fake_reply_num(SwModReply *r, const char *k, double v) {
    (void)r;
    size_t n = strlen(g_reply);
    snprintf(g_reply + n, sizeof g_reply - n, "%s=%.2f;", k, v);
}
static void fake_reply_bool(SwModReply *r, const char *k, bool v) {
    (void)r;
    size_t n = strlen(g_reply);
    snprintf(g_reply + n, sizeof g_reply - n, "%s=%s;", k, v ? "true" : "false");
}
static void fake_reply_text(SwModReply *r, const char *fmt, ...) {
    (void)r, (void)fmt;
    size_t n = strlen(g_reply);
    snprintf(g_reply + n, sizeof g_reply - n, "text;");
}
static void fake_reply_fail(SwModReply *r, const char *fmt, ...) {
    (void)r, (void)fmt;
    g_reply_failed = 1;
}
static double fake_now(void) { return g_now; }
static int fake_front(void) { return 0; }
static int fake_key(int vk) {
    (void)vk;
    return 0;
}
static int fake_pos(float out[3]) {
    memcpy(out, g_pos, sizeof g_pos);
    return 1;
}

static SwModApi g_api;
static SwModRegs g_regs;
static struct Cpu *cpu(void) { return (struct Cpu *)(void *)&g_regs; }

static void reset_mod(void) {
    t_link = 0;
    forget();
    t_last_start = -1e9;
    t_rd_phys = 0;
    memset(&g_req, 0, sizeof g_req);
    memset(&g_snap, 0, sizeof g_snap);
    g_snap.index = -1;
    t_call_works = -1;
    t_orphan_sent = 0;
    t_pw_calls = t_pw_masked = t_pw_took = 0;
    t_pw_took_name[0] = 0;
    calls_play = calls_rd = calls_generic = calls_upper = calls_stop_upper = 0;
    last_actor = 0;
    g_play_path = PATH_BODY;
    last_play[0] = 0;
    g_pos[0] = g_pos[1] = g_pos[2] = 0;
}
static void fake_link_update(struct Cpu *c) {  // the made-up Link's own update
    (void)c;
    if (W.revert && strcmp(W.cur_obj.str, "Wait")) game_plays("Wait");
}
// the made-up PlayerWait calc: the game's standing animation code (0x7000ce0c44) compares slot 0's text with Wait
static void fake_pw_calc(struct Cpu *c) {
    (void)c;
    int is_wait = !strcmp(W.cur_obj.str, "Wait");
    if (is_wait && W.cur_obj.str == W.wait_text) W.pw_saw_wait = 1;  // (our set, shown as Wait)
    if (W.pw_plays) game_plays(W.pw_plays);
    else if (W.stand_replay && !is_wait) game_plays("Wait");
}
static void step(void) {  // one frame of the made-up game: Link's AI, then his update (through our wrappers)
    g_now += 1.0 / 30.0;
    if (!W.no_pw && !child_of(W.rootai) && !child_of(W.root) && !child_of(W.normal)) {
        g_regs.x[0] = P(W.wait);
        g_pw_orig = fake_pw_calc;
        pw_calc(cpu());
    }
    g_regs.x[0] = P(W.link);
    g_orig = fake_link_update;
    link_update(cpu());
    // Link's animation step (the game: his vtable +0x390, later in the frame): a flagged body play goes on slot 1 sub 0
    // too, by slot 0's name as it reads now
    if (W.body_flag) {
        W.body_flag = 0;
        W.body1_obj.str = W.cur_obj.str;
    }
}
static int idx(const char *id) { return find_emote(id); }

SWMOD_EXPORT int emotes_selftest(char *out, int cap) {
    g_out = out, g_cap = cap, g_len = 0, g_fails = 0;
    if (cap) out[0] = 0;
    memset(&g_api, 0, sizeof g_api);
    g_api.api_version = 2;
    g_api.size = (uint32_t)offsetof(SwModApi, mod_id);  // (no settings: the defaults)
    g_api.game_version = "1.9.0";
    g_api.log = fake_log;
    g_api.guest_call = fake_call;
    g_api.reply_str = fake_reply_str, g_api.reply_int = fake_reply_int, g_api.reply_num = fake_reply_num;
    g_api.reply_bool = fake_reply_bool, g_api.reply_text = fake_reply_text, g_api.reply_fail = fake_reply_fail;
    api = &g_api;
    now_s = fake_now, in_front = fake_front, key_is_down = fake_key, player_pos = fake_pos;
    char d[160];

    // ---- names: ids, aliases, numbers, prefixes, community ids
    check("parse", "id", idx("wave") == 0 && idx("WAVE") == 0, "");
    check("parse", "alias_67", idx("67") == idx("six_seven") && idx("6 7") == idx("six_seven") && idx("six_seven") >= 0, "");
    check("parse", "alias_thrust", idx("thrust") == idx("in_love") && idx("in_love") >= 0, "");
    check("parse", "number", idx("3") == 2 && idx("25") == 24 && idx("26") == FIND_NONE, "");
    check("parse", "prefix", idx("flo") == idx("floss") && idx("s") == FIND_AMBIGUOUS, "");
    check("parse", "community", idx("my_dance") == FIND_COMMUNITY && idx("Bad!") == FIND_NONE, "");
    {
        reset_mod();
        char a0[] = "emote", a1[] = "6", a2[] = "7";
        char *argv[] = {a0, a1, a2};
        cmd_emote(3, argv, 0);
        snprintf(d, sizeof d, "kind %d index %d", g_req.kind, g_req.index);
        check("parse", "two_words", g_req.kind == REQ_PLAY && g_req.index == idx("six_seven"), d);
    }

    // ---- the state machine
    world_init(1);
    reset_mod();
    g_now = 10.0;
    step();
    snprintf(d, sizeof d, "normal %d off 0x%llx levels %d", t_ai_normal == P(W.normal),
             (unsigned long long)g_snap.ai_off, t_ai_len);
    check("ai", "found", t_ai_normal == P(W.normal) && g_snap.ai_off == LINK_ROOT_AI && t_ai_len == 3 &&
                             ai_now() == VT_WAIT, d);
    request(REQ_PLAY, idx("wave"), 0);
    step();
    snprintf(d, sizeof d, "play %s keep %llu morph %.1f", last_play, (unsigned long long)last_keep, last_morph);
    check("machine", "start", !strcmp(last_play, "ZEmote_wave") && last_keep == 1 && last_morph == -1.0f, d);
    snprintf(d, sizeof d, "after '%s' ours %d", g_snap.after, g_snap.after_ours);
    check("names", "after_same_tick", !strcmp(g_snap.after, "ZEmote_wave") && g_snap.after_ours, d);
    step();
    snprintf(d, sizeof d, "state %d slot0 '%s'", t_state, g_snap.slot0);
    check("machine", "playing", t_state == ST_PLAYING, d);
    check("names", "slot0_reads_ours", !strcmp(g_snap.slot0, "ZEmote_wave") && !mem_ok(P(g_names[0]), 1), d);
    W.finished = 1;
    step();
    snprintf(d, sizeof d, "play %s keep %llu morph %.1f state %d", last_play, (unsigned long long)last_keep,
             last_morph, t_state);
    check("machine", "finished_then_wait", !strcmp(last_play, "Wait") && last_keep == 1 && last_morph == 8.0f &&
                                               t_state == ST_IDLE, d);

    // refused while not standing (the AI's child is Move)
    g_now += 1.0;
    set_child(W.normal, 1);
    int before = calls_play;
    request(REQ_PLAY, idx("floss"), 0);
    step();
    snprintf(d, sizeof d, "why '%s'", g_snap.why);
    check("machine", "refused_moving", calls_play == before && strstr(g_snap.why, "stand still") != 0, d);
    set_child(W.normal, 0);

    // the game takes Link back
    g_now += 1.0;
    request(REQ_PLAY, idx("floss"), 0);
    step();
    step();
    check("machine", "loop_playing", t_state == ST_PLAYING && !strcmp(cur(), "ZEmote_floss"), "");
    for (int i = 0; i < 90; i++) step();
    check("machine", "loop_keeps", t_state == ST_PLAYING, "a loop never ends by itself");
    game_plays("Move");
    before = calls_play;
    step();
    step();
    check("machine", "game_took_over", t_state == ST_IDLE && calls_play == before, "nothing played after");

    // stop
    game_plays("Wait");
    g_now += 1.0;
    request(REQ_PLAY, idx("clap"), 0);
    step();
    step();
    request(REQ_STOP, -1, 0);
    step();
    check("machine", "stop", t_state == ST_IDLE && !strcmp(last_play, "Wait"), last_play);
    // the same name again later: the same pointer (the game may still hold the first)
    check("names", "interned", intern("ZEmote_clap") == intern("ZEmote_clap") && is_ours(P(intern("Wait"))), "");
    // the game keeps our char* (outside its memory): still read
    W.keep_ptr = 1;
    g_now += 1.0;
    request(REQ_PLAY, idx("clap"), 0);
    step();
    step();
    snprintf(d, sizeof d, "state %d slot0 '%s' ptr %d", t_state, g_snap.slot0, g_snap.after_ptr);
    check("names", "kept_ptr_read", t_state == ST_PLAYING && !strcmp(g_snap.slot0, "ZEmote_clap") && g_snap.after_ptr,
          d);
    request(REQ_STOP, -1, 0);
    step();
    W.keep_ptr = 0;
    game_plays("Wait");
    // the game puts Wait back on the next frame (the Game check): said so; the developer hold re-plays
    W.revert = 1;
    g_now += 1.0;
    request(REQ_PLAY, idx("floss"), 0);
    for (int i = 0; i < VERIFY_TICKS + 2; i++) step();
    snprintf(d, sizeof d, "state %d why '%s' trace '%s'", t_state, g_snap.why, g_snap.trace);
    check("revert", "said", t_state == ST_IDLE && strstr(g_snap.why, "back to standing") != 0 &&
                                strstr(g_snap.trace, "ZEmote_floss>Wait") != 0, d);
    t_hold = 1;
    g_now += 1.0;
    request(REQ_PLAY, idx("floss"), 0);
    for (int i = 0; i < 20; i++) step();
    snprintf(d, sizeof d, "state %d replays %d why '%s'", t_state, g_snap.replays, g_snap.why);
    check("revert", "hold_replays", t_state != ST_IDLE && g_snap.replays >= 15, d);
    t_hold = 0;
    W.revert = 0;
    request(REQ_STOP, -1, 0);
    step();
    t_state = ST_IDLE;
    game_plays("Wait");

    // spam guard
    g_now += 1.0;
    request(REQ_PLAY, idx("bow"), 0);
    step();
    before = calls_play;
    request(REQ_PLAY, idx("shrug"), 0);
    step();
    snprintf(d, sizeof d, "why '%s'", g_snap.why);
    check("machine", "spam_guard", calls_play == before && strstr(g_snap.why, "too quick") != 0, d);

    // a missing set: the fallback, or refused
    game_plays("Wait");
    t_state = ST_IDLE;
    g_now += 1.0;
    W.lacks[0] = "ZEmote_wave", W.lacks[1] = "ZEmote_clap";
    request(REQ_PLAY, idx("wave"), 0);
    for (int i = 0; i < VERIFY_TICKS + 2; i++) step();
    snprintf(d, sizeof d, "last %s state %d", last_play, t_state);
    check("missing", "fallback", !strcmp(last_play, "TalkHello") && t_state == ST_PLAYING, d);
    game_plays("Wait");
    t_state = ST_IDLE;
    g_now += 1.0;
    request(REQ_PLAY, idx("clap"), 0);
    for (int i = 0; i < VERIFY_TICKS + 2; i++) step();
    snprintf(d, sizeof d, "state %d why '%s'", t_state, g_snap.why);
    check("missing", "refused", t_state == ST_IDLE && strstr(g_snap.why, "animations are missing") != 0, d);
    check("missing", "why_whole", strstr(g_snap.why, "then Deploy") != 0, d);
    // it shows up late (we gave up): back to Wait, once (the made-up AI isn't in PlayerWait: its calc would do it)
    W.no_pw = 1;
    W.lacks[0] = W.lacks[1] = 0;
    game_plays("ZEmote_clap");
    step();
    snprintf(d, sizeof d, "last %s state %d", last_play, t_state);
    check("missing", "late_set_to_wait", !strcmp(last_play, "Wait") && !strcmp(cur(), "Wait") && t_state == ST_IDLE, d);
    // a game set (an NPC talk) while we track nothing: left alone
    before = calls_play;
    game_plays("TalkHello");
    step();
    step();
    check("missing", "game_talk_untouched", calls_play == before && !strcmp(cur(), "TalkHello"), last_play);
    // the play call does nothing at all: not blamed on the package
    game_plays("Wait");
    t_call_works = -1;
    W.broken = 1;
    g_now += 1.0;
    request(REQ_PLAY, idx("clap"), 0);
    for (int i = 0; i < VERIFY_TICKS + 2; i++) step();
    snprintf(d, sizeof d, "why '%s' works %d", g_snap.why, g_snap.call_works);
    check("missing", "call_fails", strstr(g_snap.why, "didn't take") != 0 && g_snap.call_works != 1, d);
    W.broken = 0;
    W.no_pw = 0;
    t_call_works = 1;
    game_plays("Wait");

    // community id: tried as ZEmote_<id>
    g_now += 1.0;
    request(REQ_PLAY, -1, "ZEmote_my_dance");
    step();
    check("machine", "community", !strcmp(last_play, "ZEmote_my_dance"), last_play);
    step();
    request(REQ_STOP, -1, 0);
    step();

    // ---- ragdoll: the pose (physics off)
    g_now += 1.0;
    request(REQ_TOGGLE, ragdoll_index(), 0);
    step();
    step();
    check("ragdoll", "pose_on", !strcmp(cur(), "ZEmote_ragdoll") && g_snap.ragdoll == 1 && calls_rd == 0, "");
    g_now += 1.0;
    request(REQ_TOGGLE, ragdoll_index(), 0);
    step();
    check("ragdoll", "pose_off", t_state == ST_IDLE && !strcmp(last_play, "Wait"), last_play);

    // ---- ragdoll: the physics (developer switch)
    g_now += 1.0;
    t_rd_phys = 1;
    game_plays("Wait");
    set_child(W.normal, 1);  // walking: refused
    for (int i = 0; i < 20; i++) step();
    request(REQ_TOGGLE, ragdoll_index(), 0);
    step();
    check("ragdoll", "phys_not_walking", calls_rd == 0 && t_state == ST_IDLE, g_snap.why);
    set_child(W.normal, 0);
    set_child(W.root, 0xffff);  // an event: PlayerNormal isn't the root's child
    for (int i = 0; i < 20; i++) step();
    g_now += 1.0;
    request(REQ_TOGGLE, ragdoll_index(), 0);
    step();
    check("ragdoll", "phys_not_in_event", calls_rd == 0, g_snap.why);
    set_child(W.root, 0);
    for (int i = 0; i < 20; i++) step();
    g_now += 1.0;
    request(REQ_TOGGLE, ragdoll_index(), 0);
    step();
    snprintf(d, sizeof d, "calls %d damage ptr ok %d", calls_rd,
             r64(W.slip, SLIP_DAMAGE) == P(&g_zero_damage));
    check("ragdoll", "phys_on", calls_rd == 1 && r64(W.slip, SLIP_DAMAGE) == P(&g_zero_damage), d);
    uint32_t seven = 7;
    memcpy(W.mgr + RD_STILL, &seven, 4);
    memcpy(W.mgr + RD_STILL2, &seven, 4);
    step();
    check("ragdoll", "stays_down", *(uint32_t *)(W.mgr + RD_STILL) == 0 && *(uint32_t *)(W.mgr + RD_STILL2) == 0, "");
    g_now += 1.0;
    request(REQ_TOGGLE, ragdoll_index(), 0);
    step();
    memcpy(W.mgr + RD_STILL, &seven, 4);
    step();
    check("ragdoll", "off_stops_writes", *(uint32_t *)(W.mgr + RD_STILL) == 7 &&
                                             r64(W.slip, SLIP_DAMAGE) == P(&g_zero_damage), "still down: kept");
    set_child(W.normal, 0);  // the game stood him up
    step();
    check("ragdoll", "damage_restored", r64(W.slip, SLIP_DAMAGE) == P(&W.damage), "");
    // a load while down: forget, write nothing to the old Link
    g_now += 1.0;
    request(REQ_TOGGLE, ragdoll_index(), 0);
    step();
    memcpy(W.other, W.link, sizeof W.other);
    w64(W.other, 0x100, 0);
    w64(W.holder, AS_OWNER, P(W.other));
    memcpy(W.mgr + RD_STILL, &seven, 4);
    request(REQ_PLAY, idx("wave"), 0);  // a key pressed during the load
    before = calls_play;
    g_regs.x[0] = P(W.other);
    tick(cpu(), P(W.other));
    check("ragdoll", "load_forgets", t_rd_child == 0 && *(uint32_t *)(W.mgr + RD_STILL) == 7, "");
    check("machine", "load_drops_request", calls_play == before && g_req.kind == REQ_NONE, "");
    t_rd_phys = 0;

    // ---- refused: in an event (the root's child isn't PlayerNormal), weapon in hand
    world_init(1);
    reset_mod();
    g_now += 5.0;
    step();
    set_child(W.root, 0xffff);
    before = calls_play;
    request(REQ_PLAY, idx("wave"), 0);
    step();
    check("guard", "event_refused", calls_play == before, g_snap.why);
    set_child(W.root, 0);
    hold_weapon(1);
    g_now += 1.0;
    request(REQ_PLAY, idx("wave"), 0);
    step();
    check("guard", "weapon_refused", calls_play == before && strstr(g_snap.why, "weapon") != 0, g_snap.why);
    hold_weapon(0);  // on his back: fine
    g_now += 1.0;
    request(REQ_PLAY, idx("wave"), 0);
    step();
    check("guard", "weapon_on_back_ok", calls_play == before + 1, g_snap.why);
    drop_weapon();

    // ---- no AI found, no PlayerWait calc seen: the body slot and speed decide
    world_init(0);
    W.no_pw = 1;
    reset_mod();
    g_now += 5.0;
    step();
    before = calls_play;
    request(REQ_PLAY, idx("robot"), 0);
    step();
    check("noai", "after_load_refused", calls_play == before, g_snap.why);
    for (int i = 0; i < 70; i++) step();
    request(REQ_PLAY, idx("robot"), 0);
    step();
    check("noai", "standing_starts", !strcmp(last_play, "ZEmote_robot"), last_play);
    t_state = ST_IDLE;
    game_plays("Wait");
    g_now += 1.0;
    before = calls_play;
    for (int i = 0; i < 3; i++) {
        g_pos[0] += 0.1f;  // 3 m/s
        step();
    }
    request(REQ_PLAY, idx("robot"), 0);
    g_pos[0] += 0.1f;
    step();
    check("noai", "moving_refused", calls_play == before, g_snap.why);
    // a talk / cutscene set was just on slot 0: refused for a moment, also once it shows Wait again
    for (int i = 0; i < 70; i++) step();
    game_plays("DemoWait");
    step();
    game_plays("Wait");
    for (int i = 0; i < 20; i++) step();
    before = calls_play;
    request(REQ_PLAY, idx("robot"), 0);
    step();
    check("noai", "event_refused", calls_play == before && strstr(g_snap.why, "cutscene") != 0, g_snap.why);
    // going up a lift: 3D speed
    for (int i = 0; i < 70; i++) step();
    for (int i = 0; i < 3; i++) {
        g_pos[1] += 0.1f;
        step();
    }
    request(REQ_PLAY, idx("robot"), 0);
    g_pos[1] += 0.1f;
    step();
    check("noai", "lift_refused", calls_play == before, g_snap.why);

    // ---- PlayerWait's calc: the game's standing code puts Wait back; the mask keeps our set (the fix)
    world_init(1);
    reset_mod();
    g_now += 5.0;
    for (int i = 0; i < 3; i++) step();
    request(REQ_PLAY, idx("wave"), 0);
    step();
    for (int i = 0; i < 6; i++) step();
    snprintf(d, sizeof d, "state %d slot0 '%s' masked %lld took %lld saw_wait %d trace '%s'", t_state, cur(),
             (long long)t_pw_masked, (long long)t_pw_took, W.pw_saw_wait, g_snap.trace);
    check("pw", "replay_blocked", t_state == ST_PLAYING && !strcmp(cur(), "ZEmote_wave") && t_pw_masked >= 5 &&
                                      t_pw_took == 0 && W.pw_saw_wait && strstr(g_snap.trace, "ZEmote_wave>ZEmote_wave"),
          d);
    check("pw", "name_restored", W.cur_obj.str == wname("ZEmote_wave") && !t_masked && !g_snap.masked, "");
    W.finished = 1;
    step();
    check("pw", "finish_releases", t_state == ST_IDLE && !strcmp(last_play, "Wait") && !t_name_at &&
                                       W.cur_obj.str == wname("Wait"), last_play);
    // the game plays a standing set of its own during that calc (cold, out of breath...): it wins, untouched
    g_now += 1.0;
    request(REQ_PLAY, idx("clap"), 0);
    step();
    step();  // playing
    W.pw_plays = "StaminaOut";
    int before_pw = calls_play;
    step();
    W.pw_plays = 0;
    snprintf(d, sizeof d, "state %d slot0 '%s' took %lld '%s'", t_state, cur(), (long long)t_pw_took, t_pw_took_name);
    check("pw", "game_set_in_calc_wins", t_state == ST_IDLE && !strcmp(cur(), "StaminaOut") && calls_play == before_pw &&
                                             t_pw_took == 1 && !strcmp(t_pw_took_name, "StaminaOut"), d);
    // mask off (developer A/B): the standing code puts Wait back inside PlayerWait's calc, and it is counted
    game_plays("Wait");
    for (int i = 0; i < 3; i++) step();
    g_mask_mode = MASK_OFF;
    g_now += 1.0;
    request(REQ_PLAY, idx("floss"), 0);
    for (int i = 0; i < VERIFY_TICKS + 2; i++) step();
    snprintf(d, sizeof d, "state %d took %lld '%s' why '%s'", t_state, (long long)t_pw_took, t_pw_took_name, g_snap.why);
    check("pw", "took_when_off", t_state == ST_IDLE && t_pw_took == 2 && !strcmp(t_pw_took_name, "Wait") &&
                                     strstr(g_snap.why, "back to standing") != 0, d);
    // mask always: Wait the whole emote, also against a replay outside PlayerWait's calc (Link's update here)
    g_mask_mode = MASK_ALWAYS;
    W.revert = 1;
    g_now += 1.0;
    request(REQ_PLAY, idx("floss"), 0);
    for (int i = 0; i < 10; i++) step();
    snprintf(d, sizeof d, "state %d masked %d str_is_mask %d slot0 '%s'", t_state, g_snap.masked,
             W.cur_obj.str == W.wait_text, g_snap.slot0);
    check("pw", "always_mode", t_state == ST_PLAYING && g_snap.masked && W.cur_obj.str == W.wait_text &&
                                   !strcmp(g_snap.slot0, "ZEmote_floss"), d);
    request(REQ_STOP, -1, 0);
    step();
    snprintf(d, sizeof d, "state %d last %s slot0 '%s' str_is_mask %d", t_state, last_play, cur(),
             W.cur_obj.str == W.wait_text);
    check("pw", "always_stop_unmasks", t_state == ST_IDLE && !strcmp(last_play, "Wait") && !t_masked &&
                                           W.cur_obj.str == wname("Wait"), d);
    W.revert = 0;
    g_mask_mode = MASK_CALC;
    // no AI tree found, but PlayerWait's calc runs: it alone says Link stands (and refuses while it doesn't run)
    world_init(0);
    reset_mod();
    g_now += 5.0;
    for (int i = 0; i < 3; i++) step();
    request(REQ_PLAY, idx("robot"), 0);
    step();
    snprintf(d, sizeof d, "last %s ai %d pw_frames %d why '%s'", last_play, t_ai_normal != 0, t_pw_frames, g_snap.why);
    check("pw", "standing_from_pw", !strcmp(last_play, "ZEmote_robot") && !t_ai_normal, d);
    request(REQ_STOP, -1, 0);
    step();
    set_child(W.normal, 1);  // he walks: PlayerWait's calc stops
    for (int i = 0; i < 3; i++) step();
    g_now += 1.0;
    before_pw = calls_play;
    request(REQ_PLAY, idx("robot"), 0);
    step();
    check("pw", "moving_refused", calls_play == before_pw && strstr(g_snap.why, "stand still") != 0, g_snap.why);
    // the first try's AI layout (PlayerRoot found by scanning Link) still works
    world_init(2);
    reset_mod();
    step();
    snprintf(d, sizeof d, "normal %d off 0x%llx levels %d", t_ai_normal == P(W.normal),
             (unsigned long long)g_snap.ai_off, t_ai_len);
    check("ai", "scan_fallback", t_ai_normal == P(W.normal) && g_snap.ai_off == 0x100 && t_ai_len == 2 &&
                                     ai_now() == VT_WAIT, d);
    // an event: the RootAi's current child isn't PlayerRoot
    world_init(1);
    reset_mod();
    step();
    set_child(W.rootai, 0xffff);
    check("ai", "event_above_root", ai_now() == 0, "");

    // ---- the play path (Game check r2: slot 0 held our set, the model stood idle): Link's own body play flags him,
    // so his animation step puts the set on slot 1 sub 0 (the upper body) too; the generic play didn't
    world_init(1);
    reset_mod();
    g_now += 5.0;
    for (int i = 0; i < 3; i++) step();
    request(REQ_PLAY, idx("wave"), 0);
    step();
    snprintf(d, sizeof d, "generic %d actor_is_link %d keep %llu slot0 '%s' body1 '%s'", calls_generic,
             last_actor == P(W.link), (unsigned long long)last_keep, cur(), W.body1_obj.str);
    check("path", "body_play", calls_generic == 0 && last_actor == P(W.link) && last_keep == 1 &&
                                   !strcmp(cur(), "ZEmote_wave") && !strcmp(W.body1_obj.str, "ZEmote_wave"), d);
    step();
    snprintf(d, sizeof d, "body1 '%s' upper '%s'", g_snap.body1, g_snap.upper);
    check("path", "upper_body_seen", t_state == ST_PLAYING && !strcmp(g_snap.body1, "ZEmote_wave") && !g_snap.upper[0],
          d);
    W.finished = 1;
    step();
    snprintf(d, sizeof d, "slot0 '%s' body1 '%s'", cur(), W.body1_obj.str);
    check("path", "wait_on_both", t_state == ST_IDLE && !strcmp(cur(), "Wait") && !strcmp(W.body1_obj.str, "Wait"), d);
    // A/B (emotedbg path generic): the r2 call, slot 0 only: the upper body stays on Wait
    g_play_path = PATH_GENERIC;
    g_now += 1.0;
    request(REQ_PLAY, idx("floss"), 0);
    step();
    step();
    snprintf(d, sizeof d, "generic %d slot0 '%s' body1 '%s'", calls_generic, cur(), W.body1_obj.str);
    check("path", "generic_legs_only", calls_generic == 1 && t_state == ST_PLAYING && !strcmp(cur(), "ZEmote_floss") &&
                                           !strcmp(W.body1_obj.str, "Wait"), d);
    request(REQ_STOP, -1, 0);
    step();
    g_play_path = PATH_BODY;
    // the developer control: an upper-body set the game's own way (TalkHello as in a talk), stopped when it ends
    for (int i = 0; i < 3; i++) step();
    request(REQ_DBG_UPPER, -1, "TalkHello");
    step();
    snprintf(d, sizeof d, "upper calls %d '%s' dbg '%s'", calls_upper, W.upper_obj.str, t_dbg_upper);
    check("path", "upper_dbg_plays", calls_upper == 1 && !strcmp(W.upper_obj.str, "TalkHello") &&
                                         !strcmp(t_dbg_upper, "TalkHello") && !strcmp(cur(), "Wait"), d);
    for (int i = 0; i < 4; i++) step();
    snprintf(d, sizeof d, "stops %d dbg '%s' snap '%s'", calls_stop_upper, t_dbg_upper, g_snap.upper);
    check("path", "upper_dbg_runs", calls_stop_upper == 0 && !strcmp(t_dbg_upper, "TalkHello") &&
                                        !strcmp(g_snap.upper, "TalkHello"), d);
    W.upper_finished = 1;
    step();
    snprintf(d, sizeof d, "stops %d upper '%s' dbg '%s'", calls_stop_upper, W.upper_obj.str, t_dbg_upper);
    check("path", "upper_dbg_stops", calls_stop_upper == 1 && !W.upper_obj.str[0] && !t_dbg_upper[0], d);
    request(REQ_DBG_UPPER, -1, "TalkYes");
    step();
    request(REQ_DBG_UPPER, -1, "-");
    step();
    snprintf(d, sizeof d, "stops %d upper '%s'", calls_stop_upper, W.upper_obj.str);
    check("path", "upper_dbg_stop_cmd", calls_stop_upper == 2 && !W.upper_obj.str[0] && !t_dbg_upper[0], d);

    // ---- emote state (for multiplayer): nothing played yet = no id
    world_init(1);
    reset_mod();
    step();
    g_reply[0] = 0;
    char s0[] = "emote", s1[] = "state";
    char *sv[] = {s0, s1};
    cmd_emote(2, sv, 0);
    check("state", "fields", strstr(g_reply, "has_emote=true") && strstr(g_reply, "list=wave,") &&
                                 strstr(g_reply, "ticks="), g_reply);
    check("state", "no_id_at_boot", strstr(g_reply, "id=;") != 0, g_reply);
    return g_fails;
}
