import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, AppState, Image, KeyboardAvoidingView, Modal, Platform, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View, useWindowDimensions } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { Feather, MaterialCommunityIcons } from '@expo/vector-icons';
import { useFonts } from 'expo-font';
import { StatusBar } from 'expo-status-bar';
import * as api from './api';

const C = { navy: '#0c2a44', blue: '#244b66', paper: '#f7f5f2', beige: '#e0d8d1', muted: '#71808d', line: '#e8e4df', white: '#fff', green: '#26725b', orange: '#976528', red: '#a1443d' };
const names: any = { pending: 'بانتظار المخزن', requested: 'بانتظار المخزن', unrequested: 'لم تطلب الخامات', waiting_receipt: 'جاهز للاستلام', issued: 'تم الصرف', ready: 'جاهز للبدء', in_progress: 'قيد التشغيل', done: 'مكتمل', started: 'بدأ التشغيل', approved: 'معتمد', rejected: 'مرفوض', cancelled: 'ملغي', waiting: 'بانتظار استلامك', full: 'مستلم بالكامل', partial: 'استلام جزئي', not_issued: 'لم يتم الصرف', legacy: 'صرف سابق', pass: 'الجودة مقبولة', reject: 'الجودة مرفوضة' };
const number = (value: any) => new Intl.NumberFormat('en', { maximumFractionDigits: 2 }).format(Number(value) || 0);
const formatDate = (value: string) => value ? new Date(value.includes('T') ? value : value.replace(' ', 'T') + 'Z').toLocaleString('ar-EG', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
const jobKey = (job: any) => job.batch_token || `order:${job.id}`;
const isRunning = (job: any) => job.state === 'in_progress' || job.can_finish || job.can_finish_stage || Number(job.working_qty) > 0;
function Txt({ children, style, ...rest }: any) { return <Text {...rest} style={[s.text, style]}>{children}</Text>; }
function Icon({ name, size = 20, color = C.navy }: any) { return <Feather name={name} size={size} color={color} />; }
function Button({ title, onPress, icon, tone = 'primary', disabled, testID }: any) {
    return <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={title} disabled={disabled} onPress={onPress} style={({ pressed }) => [s.button, tone === 'primary' ? s.primary : tone === 'danger' ? s.danger : s.secondary, pressed && { opacity: .75 }, disabled && { opacity: .45 }]}>
        <Txt style={[s.buttonText, { color: tone === 'primary' ? C.white : tone === 'danger' ? C.red : C.navy }]}>{title}</Txt>
        {icon && <Icon name={icon} size={17} color={tone === 'primary' ? C.white : C.navy} />}
    </Pressable>;
}
function Badge({ label, tone = 'neutral' }: any) { return <View style={[s.badge, { backgroundColor: tone === 'green' ? '#eaf4ee' : tone === 'orange' ? '#fbf0df' : '#edf0f3' }]}><Txt style={{ fontSize: 11, color: tone === 'green' ? C.green : tone === 'orange' ? C.orange : C.blue }}>{label}</Txt></View>; }
function Empty({ icon = 'inbox', title, text }: any) { return <View style={s.empty}><Icon name={icon} size={35} color={C.muted}/><Txt style={s.h3}>{title}</Txt><Txt style={s.muted}>{text}</Txt></View>; }
function Field({ label, value, onChangeText, secureTextEntry, keyboardType, placeholder, testID }: any) { return <View style={{ gap: 7 }}><Txt style={s.label}>{label}</Txt><TextInput testID={testID} accessibilityLabel={label} style={s.input} value={value} onChangeText={onChangeText} secureTextEntry={secureTextEntry} keyboardType={keyboardType} autoCapitalize="none" autoCorrect={false} placeholder={placeholder} placeholderTextColor="#a3aaa9" /></View>; }

export default function App() {
    const [fonts] = useFonts({ Tajawal: require('./assets/Tajawal-Regular.ttf'), TajawalBold: require('./assets/Tajawal-Bold.ttf') });
    return <SafeAreaProvider><StatusBar style="dark"/>{fonts ? <SupervisorApp/> : <View style={s.loading}><ActivityIndicator color={C.navy}/></View>}</SafeAreaProvider>;
}
function SupervisorApp() {
    const { width } = useWindowDimensions();
    const desktop = width >= 960;
    const [profile, setProfile] = useState<any>(null);
    const [booting, setBooting] = useState(true);
    const [loginName, setLoginName] = useState(''); const [password, setPassword] = useState('');
    const [otp, setOtp] = useState(''); const [mfa, setMfa] = useState(false); const [showPassword, setShowPassword] = useState(false);
    const [loginError, setLoginError] = useState(''); const [busy, setBusy] = useState(false); const lock = useRef(false);
    const [tab, setTab] = useState('home'); const [stage, setStage] = useState(''); const stageRef = useRef('');
    const [dashboard, setDashboard] = useState<any>(null); const [requests, setRequests] = useState<any[]>([]);
    const [requestPage, setRequestPage] = useState({ has_more: false, next_offset: 0 });
    const [warnings, setWarnings] = useState<any[]>([]); const [filter, setFilter] = useState('all'); const [query, setQuery] = useState('');
    const [detail, setDetail] = useState<any>(null); const [form, setForm] = useState<any>(null);
    const [requestDetail, setRequestDetail] = useState<any>(null); const [confirm, setConfirm] = useState<any>(null);
    const [notice, setNotice] = useState<any>(null); const [refreshing, setRefreshing] = useState(false);
    const sequence = useRef(0); const profileRef = useRef<any>(null); const mounted = useRef(true);
    const [lastSync, setLastSync] = useState('');
    const error = (e: any) => { if (e.auth) { setProfile(null); profileRef.current = null; setDashboard(null); setPassword(''); setForm(null); setDetail(null); setRequestDetail(null); setConfirm(null); setNotice(null); setLoginError('انتهت الجلسة. سجّل دخولك مرة أخرى.'); } else setNotice({ error: true, text: e.message || 'تعذّر إكمال العملية.' }); };
    async function refresh(code = stageRef.current, quiet = false) {
        const n = ++sequence.current;
        if (!quiet) setRefreshing(true);
        try {
            const [data, material, alerts] = await Promise.all([api.call('dashboard', { stage: code }), api.call('requests'), api.call('warnings')]);
            if (!mounted.current || n !== sequence.current) return;
            setDashboard(data); setStage(data.selected_stage); stageRef.current = data.selected_stage;
            setRequests(material.rows); setRequestPage(material); setWarnings(alerts);
            setLastSync(new Date().toLocaleTimeString('ar-EG', { hour: 'numeric', minute: '2-digit' }));
        } catch (e) { if (n === sequence.current) error(e); }
        finally { if (n === sequence.current && mounted.current) setRefreshing(false); }
    }
    async function loadProfile() {
        const value = await api.call('profile'); setProfile(value); profileRef.current = value;
        const code = value.stages[0]?.code || ''; setStage(code); stageRef.current = code;
        await refresh(code);
    }
    useEffect(() => {
        mounted.current = true;
        (async () => { try { await api.restoreToken(); await loadProfile(); } catch (e: any) { if (!e.auth) setLoginError(e.message); } finally { setBooting(false); } })();
        return () => { mounted.current = false; sequence.current++; };
    }, []);
    useEffect(() => {
        const timer = setInterval(() => { if (profileRef.current && !lock.current && AppState.currentState === 'active' && (Platform.OS !== 'web' || !document.hidden)) refresh(stageRef.current, true); }, 60000);
        return () => clearInterval(timer);
    }, []);
    async function signIn() {
        if (lock.current) return; lock.current = true; setBusy(true); setLoginError('');
        try {
            const result = await api.login(loginName, password, otp);
            if (result.mfa_required) { setMfa(true); setLoginError('اكتب كود التحقق من تطبيق المصادقة.'); return; }
            setPassword(''); setOtp(''); setMfa(false); await loadProfile();
        } catch (e: any) { setLoginError(e.message); }
        finally { lock.current = false; setBusy(false); }
    }
    async function changeStage(code: string) {
        if (lock.current || refreshing || code === stageRef.current) return;
        setDetail(null); setForm(null); setStage(code); stageRef.current = code; setDashboard(null); setFilter('all'); await refresh(code);
    }
    const jobs = dashboard ? (dashboard.batch_supervisor_mode ? dashboard.product_batches || [] : dashboard.orders || []) : [];
    const stageInfo = (dashboard?.stages || []).find((row: any) => row.code === stage) || {};
    const stageLabel = (code: string) => profile?.stages.find((row: any) => row.code === code)?.label || code;
    const handoffs = dashboard?.pending_handoffs || [];
    const completed = dashboard?.history?.completed || [];
    const waiting = dashboard?.history?.waiting || [];
    const filteredJobs = jobs.filter((job: any) => (!query || [job.product_name, job.model_name, job.name, ...(job.product_lines || []).map((p: any) => p.product_name)].join(' ').includes(query)) && (filter === 'all' || (filter === 'running' ? isRunning(job) : filter === 'ready' ? job.can_start || job.can_start_stage : true)));
    const warningStages = ['painting', 'carpentry', 'bases', 'finishing', 'upholstery', 'packaging'];
    function actionData(action: string, job: any, product?: any, extra = {}) {
        const data: any = { action, stage, ...extra };
        if (job.batch_token) data.batch_token = job.batch_token;
        else {
            data.order_id = job.id;
            if (product) data.line_id = product.production_line_id;
            const lines = product ? [product] : job.product_lines || [];
            data.line_ids = [...new Set(lines.flatMap((row: any) => action === 'quality' ? row.quality_production_line_ids || [] : row.startable_production_line_ids || []))];
        }
        return data;
    }
    async function execute(data: any, operation = 'action') {
        if (lock.current) return; lock.current = true; setBusy(true); setConfirm(null); setNotice(null);
        try {
            const result = await api.call(operation, { ...data, request_id: api.requestId() });
            if (result.form) { setRequestDetail(null); setForm({ ...result.form, stage: data.stage }); }
            else { setForm(null); setDetail(null); setNotice({ text: result.warning?.message ? result.message + '\n' + result.warning.message : result.message }); }
            if (!result.form) await refresh(stageRef.current, true);
        } catch (e) { error(e); }
        finally { lock.current = false; setBusy(false); }
    }
    function chooseAction(action: string, job: any, product?: any, decision?: string) {
        const data = actionData(action, job, product, decision ? { decision } : {});
        if (action === 'bom' || action === 'warning') execute(data);
        else setConfirm({ title: ({ materials: 'إرسال طلب الخامات؟', receive: 'تأكيد استلام الخامات المصروفة بالكامل؟', start: 'بدء تشغيل الشغل المختار؟', finish: 'إنهاء الشغل وتسجيله على النظام؟', pause: 'إيقاف مؤقت لوقت الشغل؟', resume: 'استئناف وقت الشغل؟', quality: decision === 'pass' ? 'اعتماد فحص الجودة؟' : 'تسجيل رفض الجودة؟' } as any)[action], text: 'سيتم التسجيل على حسابك في نظام المصنع.', data });
    }
    function jobActions(job: any, product?: any) {
        const batch = Boolean(job.batch_token);
        return <View style={s.actionStack}>
            {!product && <>
                {job.timer && <Txt style={s.hint}>{job.timer.paused ? 'الوقت متوقف مؤقتًا' : 'وقت الشغل المتبقي'}: {number(Math.abs((job.timer.remaining_seconds || 0) / 3600))} ساعة{job.timer.remaining_seconds < 0 ? ' تأخير' : ''}</Txt>}
                {job.timer?.can_pause && <Button title="إيقاف مؤقت" icon="pause" tone="secondary" disabled={busy} onPress={() => chooseAction('pause', job)}/>}
                {job.timer?.can_resume && <Button title="استئناف الشغل" icon="play" tone="secondary" disabled={busy} onPress={() => chooseAction('resume', job)}/>}
                {job.can_request_materials && <Button title="طلب الخامات" icon="box" disabled={busy} onPress={() => chooseAction('materials', job)}/>}
                {job.can_receive_materials && <Button title="استلام الخامات" icon="download" disabled={busy} onPress={() => chooseAction('receive', job)}/>}
                {(job.can_start || job.can_start_stage) && <Button title="بدء التشغيل" icon="play" disabled={busy} onPress={() => chooseAction('start', job)}/>}
                {(job.can_finish || job.can_finish_stage) && <Button title="إنهاء الشغل" icon="check-circle" disabled={busy || !job.quality_ready} onPress={() => chooseAction('finish', job)}/>}
                {(job.can_finish || job.can_finish_stage) && !job.quality_ready && <Txt style={s.hint}>كمّل فحص الجودة قبل إنهاء الشغل.</Txt>}
            </>}
            {!job.textile_kit && product?.can_start_stage && <Button title="بدء هذا الصنف" disabled={busy} onPress={() => chooseAction('start', job, product)}/>}
            {(product?.can_review_quality || (!product && batch && job.can_review_quality)) && <View style={s.twoButtons}><Button title="قبول الجودة" tone="secondary" disabled={busy} onPress={() => chooseAction('quality', job, product, 'pass')}/><Button title="رفض الجودة" tone="danger" disabled={busy} onPress={() => chooseAction('quality', job, product, 'reject')}/></View>}
            {(product?.can_open_bom || (!product && batch && job.can_open_bom)) && <Button title="تفاصيل الخامات المطلوبة" icon="layers" tone="secondary" disabled={busy} onPress={() => chooseAction('bom', job, product)}/>}
            {!profile.is_manager && warningStages.includes(stage) && (product || batch) && <Button title="إرسال تحذير إنتاج" icon="alert-triangle" tone="secondary" disabled={busy} onPress={() => chooseAction('warning', job, product)}/>}
        </View>;
    }
    async function openDetail(job: any) {
        setNotice(null); setDetail(job);
        if (job.has_order_image && !job.batch_token) {
            try {
                const res = await api.call('order_image', { stage, order_id: job.id });
                setDetail((current: any) => current && jobKey(current) === jobKey(job) ? { ...current, image: res.uri } : current);
            } catch (e) { error(e); }
        }
    }
    const sheetNotice = notice?.error ? <View style={s.errorBox}><Txt style={{ color: C.red }}>{notice.text}</Txt></View> : null;
    function jobCard(job: any, compact = false) {
        const title = job.product_name || job.name || job.product_lines?.[0]?.product_name || 'أمر تشغيل';
        return <Pressable key={jobKey(job)} accessibilityRole="button" accessibilityLabel={'تفاصيل ' + title} onPress={() => openDetail(job)} style={({ pressed }) => [s.job, pressed && { borderColor: C.blue }]}>
            <View style={s.rowBetween}><Badge label={isRunning(job) ? 'قيد التشغيل' : job.can_start || job.can_start_stage ? 'جاهز للبدء' : names[job.state] || 'مخطط'} tone={isRunning(job) ? 'green' : 'orange'}/><Txt style={s.small}>{job.model_name || 'شغل المرحلة'}</Txt></View>
            <View style={s.jobMain}><Icon name="chevron-left" color={C.muted}/><View style={{ flex: 1, gap: 5 }}><Txt style={s.h3}>{title}</Txt><Txt style={s.muted}>{job.dimension_label || (job.product_lines?.map((p: any) => p.product_name).join(' · ')) || stageLabel(stage)}</Txt></View><View style={s.jobIcon}><MaterialCommunityIcons name="sofa-outline" size={32} color={C.navy}/></View></View>
            <View style={s.jobFooter}><Txt style={s.link}>عرض التفاصيل</Txt><Txt style={s.small}><Txt style={s.bold}>{number(job.display_qty ?? job.quantity ?? job.planned_qty)}</Txt> {job.uom_name || 'قطعة'} {isRunning(job) ? ' • الشغل بدأ' : ''}</Txt></View>
        </Pressable>;
    }
    function header(title: string, subtitle?: string) { return <View style={{ gap: 5, marginBottom: 20 }}><Txt style={s.h1}>{title}</Txt>{subtitle && <Txt style={s.muted}>{subtitle}</Txt>}</View>; }
    const stagePicker = <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0, marginBottom: 18 }} contentContainerStyle={{ flexDirection: 'row-reverse', gap: 8 }}>
        {profile?.stages.map((item: any) => <Pressable key={item.code} accessibilityRole="button" accessibilityLabel={'مرحلة ' + item.label} onPress={() => changeStage(item.code)} disabled={busy || refreshing} style={[s.chip, stage === item.code && s.chipActive]}><Txt style={[s.chipText, stage === item.code && { color: C.white }]}>{item.label}</Txt></Pressable>)}
    </ScrollView>;

    if (booting) return <SafeAreaView style={s.loading}><Image source={require('./assets/logo.png')} style={s.logo}/><ActivityIndicator color={C.navy}/><Txt style={s.muted}>جارٍ الاتصال بالمصنع…</Txt></SafeAreaView>;
    if (!profile) return <SafeAreaView style={s.screen}><KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}><View style={[s.loginLayout, desktop && { flexDirection: 'row' }]}>
        {desktop && <View style={s.loginArt}><Image source={require('./assets/logo-light.png')} style={{ width: 230, height: 65, resizeMode: 'contain' }}/><View style={s.artLines}><MaterialCommunityIcons name="sofa-outline" size={180} color="#d5cbc0"/></View><Txt style={s.artTitle}>كل يوم شغل،{ '\n' }خطوة لقدّام.</Txt><Txt style={s.artCopy}>من أول قطعة لحد آخر تسليم.{ '\n' }مكان واحد لشغلك في مابيلا.</Txt><View style={s.artFooter}><View style={s.dot}/><Txt style={{ color: C.beige }}>MAPILLA • FACTORY TEAM</Txt></View></View>}
        <ScrollView contentContainerStyle={[s.loginContent, desktop && { maxWidth: 570 }]} keyboardShouldPersistTaps="handled">
            <Image source={require('./assets/logo.png')} style={[s.logo, { width: 150, height: 43, alignSelf: 'flex-end', marginBottom: 46 }]}/>
            <View style={s.loginMark}><Icon name="user" size={27}/></View><Txt style={s.eyebrow}>تطبيق مشرفي المصنع</Txt><Txt style={s.loginTitle}>أهلاً بيك في فريق مابيلا</Txt><Txt style={[s.muted, { marginBottom: 30, lineHeight: 25 }]}>سجّل دخولك وابدأ يومك.{ '\n' }شغلك وخاماتك وتحديثات فريقك، كلهم هنا.</Txt>
            <View style={{ gap: 18 }}><Field testID="login-user" label="اسم المستخدم" placeholder="اسم المستخدم على النظام" value={loginName} onChangeText={setLoginName}/>
                <View><Field testID="login-password" label="كلمة المرور" placeholder="اكتب كلمة المرور" value={password} onChangeText={setPassword} secureTextEntry={!showPassword}/><Pressable accessibilityLabel={showPassword ? 'إخفاء كلمة المرور' : 'إظهار كلمة المرور'} onPress={() => setShowPassword(!showPassword)} style={s.eye}><Icon name={showPassword ? 'eye-off' : 'eye'} size={19} color={C.muted}/></Pressable></View>
                {mfa && <Field label="كود التحقق" value={otp} onChangeText={setOtp} keyboardType="number-pad" placeholder="000000"/>}
                {Boolean(loginError) && <View style={s.errorBox}><Txt style={{ color: C.red }}>{loginError}</Txt></View>}
                <Button testID="login-submit" title={busy ? 'جارٍ تسجيل الدخول…' : 'تسجيل الدخول'} icon="arrow-left" disabled={busy || !loginName || !password} onPress={signIn}/>
            </View><View style={s.loginFoot}><Icon name="shield" size={16} color={C.muted}/><Txt style={s.small}>بنفس حسابك وصلاحياتك في نظام المصنع</Txt></View><Txt style={[s.small, { textAlign: 'center', marginTop: 38 }]}>لو مش قادر تدخل، تواصل مع مسؤول النظام.</Txt>
        </ScrollView>
    </View></KeyboardAvoidingView></SafeAreaView>;

    return <SafeAreaView style={s.screen} edges={['top','bottom']}>
        <View style={s.topbar}><Pressable accessibilityLabel="حسابي" onPress={() => setTab('account')} style={s.avatar}><Txt style={s.avatarText}>{profile.name?.trim().slice(0, 1)}</Txt></Pressable><View style={{ flex: 1 }}/><Image source={require('./assets/logo.png')} style={s.logo}/></View>
        <View style={[s.appBody, desktop && { maxWidth: 1100, alignSelf: 'center', width: '100%' }]}>
            {notice && <View style={[s.notice, notice.error && s.errorBox]}><Pressable accessibilityLabel="إغلاق الرسالة" onPress={() => setNotice(null)}><Icon name="x" size={18}/></Pressable><Txt style={[{ flex: 1, lineHeight: 23 }, notice.error && { color: C.red }]}>{notice.text}</Txt></View>}
            <ScrollView style={{ flex: 1 }} contentContainerStyle={s.content} keyboardShouldPersistTaps="handled" refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => !busy && refresh()} tintColor={C.navy}/> }>
                {tab === 'home' && <>
                    <View style={s.rowBetween}><Pressable accessibilityRole="button" accessibilityLabel="تحديث البيانات" onPress={() => !busy && refresh()} style={s.refresh}><Icon name="refresh-cw" size={18}/></Pressable><Txt style={s.small}>{new Date().toLocaleDateString('ar-EG', { weekday: 'long', day: 'numeric', month: 'long' })}</Txt></View>
                    <Txt style={[s.h1, { marginTop: 14 }]}>يومك سعيد، {profile.name.split(' ')[0]}</Txt><Txt style={[s.muted, { marginTop: 5, marginBottom: 22 }]}>خلّينا نطمن على الشغل، خطوة بخطوة.</Txt>
                    {stagePicker}
                    <View style={s.summary}><View style={{ flex: 1 }}><Txt style={s.summaryCaption}>ملخص شغلك · {stageLabel(stage)}</Txt><Txt style={s.summaryNumber}>{number(stageInfo.working_qty)}</Txt><Txt style={{ color: '#e3e8ec' }}>قطعة قيد التشغيل الآن</Txt></View><View style={s.summaryArt}><MaterialCommunityIcons name="sofa-outline" size={72} color={C.beige}/></View></View>
                    <View style={s.statsRow}><View style={s.stat}><View style={[s.statIcon, { backgroundColor: '#f8f0e6' }]}><Icon name="clock" color={C.orange}/></View><Txt style={s.statNum}>{number(stageInfo.remaining_qty)}</Txt><Txt style={s.small}>قطعة متبقية</Txt></View><View style={s.stat}><View style={[s.statIcon, { backgroundColor: '#eaf4ee' }]}><Icon name="check" color={C.green}/></View><Txt style={s.statNum}>{number(dashboard?.history?.completed_quantity ?? stageInfo.completed_qty)}</Txt><Txt style={s.small}>مكتمل في المرحلة</Txt></View></View>
                    {handoffs.length > 0 && <Pressable onPress={() => setTab('alerts')} style={s.attention}><Icon name="chevron-left" color={C.orange}/><View style={{ flex: 1 }}><Txt style={s.bold}>{number(handoffs.length)} تحويل بانتظارك</Txt><Txt style={s.small}>راجع الجودة واستلم الشغل من المرحلة السابقة</Txt></View><Icon name="truck" color={C.orange}/></Pressable>}
                    <View style={s.sectionHeading}><Pressable onPress={() => { setTab('work'); setFilter('all'); }}><Txt style={s.link}>كل الشغل <Icon name="arrow-left" size={13}/></Txt></Pressable><Txt style={s.h2}>شغلك الحالي</Txt></View>
                    {jobs.length ? jobs.slice(0, 3).map((job: any) => jobCard(job, true)) : <Empty title={refreshing ? 'بنحمّل شغلك…' : 'مافيش شغل متاح حاليًا'} text="الشغل الجديد هيظهر هنا بمجرد إتاحته لمرحلتك."/>}
                    <View style={s.quickRow}><Pressable onPress={() => setTab('materials')} style={s.quick}><Icon name="package"/><Txt style={s.bold}>الخامات</Txt><Txt style={s.small}>طلباتك وحالة الصرف</Txt></Pressable><Pressable onPress={() => setTab('alerts')} style={s.quick}><Icon name="bell"/><Txt style={s.bold}>تحديثات الفريق</Txt><Txt style={s.small}>تحذيرات وتحويلات</Txt></Pressable></View>
                </>}
                {tab === 'work' && <>
                    {header('الشغل', 'تفاصيل واضحة، وخطوتك الجاية قدامك.')}{stagePicker}
                    <View style={s.search}><Icon name="search" size={18} color={C.muted}/><TextInput accessibilityLabel="ابحث في الشغل" style={s.searchInput} value={query} onChangeText={setQuery} placeholder="ابحث بالصنف أو الموديل…" placeholderTextColor={C.muted}/></View>
                    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.filters}>{[['all','الكل'],['running','قيد التشغيل'],['ready','جاهز'],['history','المكتمل'],['waiting','قادم']].map(([key,label]) => <Pressable key={key} onPress={() => setFilter(key)} style={[s.filter, filter === key && s.filterActive]}><Txt style={[s.small, filter === key && s.bold]}>{label}</Txt></Pressable>)}</ScrollView>
                    {filter === 'history' || filter === 'waiting' ? <>{(filter === 'history' ? completed : waiting).map((row: any) => <View key={row.key} style={s.job}><View style={s.rowBetween}><Badge label={filter === 'history' ? 'مكتمل' : 'بانتظار المرحلة السابقة'}/><Txt style={s.small}>{row.model_name}</Txt></View><Txt style={s.h3}>{row.product_name}</Txt><Txt style={s.muted}>{row.dimension_label}</Txt><Txt style={s.bold}>{number(row.quantity)} {row.uom_name}</Txt><Txt style={s.small}>{formatDate(row.finished_at)}</Txt></View>)}{!(filter === 'history' ? completed : waiting).length && <Empty title="لا يوجد شغل في القائمة دي" text="جرّب اختيار قائمة أو مرحلة تانية."/>}</> : <>{filteredJobs.map((job: any) => jobCard(job))}{!filteredJobs.length && <Empty title="لا توجد نتائج" text="جرّب تغيير البحث أو فلتر حالة الشغل."/>}</>}
                </>}
                {tab === 'materials' && <>
                    {header('الخامات', 'من الطلب للاستلام، تابع كل خطوة.')}{stagePicker}{profile.supply_stages?.includes(stage) && <View style={{ marginBottom: 12 }}><Button title="طلب تغذية رصيد الصالة" icon="truck" disabled={busy} onPress={() => execute({ action: 'supply_form', stage })}/></View>}<Button title="طلب خامات لشغل جديد" icon="plus" onPress={() => { setTab('work'); setFilter('all'); }}/>
                    <View style={s.sectionHeading}><Txt style={s.small}>{number(requests.length)} طلب معروض</Txt><Txt style={s.h2}>طلبات مراحل حسابك</Txt></View>
                    {requests.map(row => <Pressable accessibilityRole="button" accessibilityLabel={'تفاصيل إذن ' + row.name} key={row.key} style={s.job} onPress={() => setRequestDetail(row)}><View style={s.rowBetween}><Badge label={names[row.state] || row.state} tone={['pending','requested'].includes(row.state) ? 'orange' : 'green'}/><Txt style={s.bold}>{row.name}</Txt></View><Txt style={s.h3}>{stageLabel(row.stage_code)}</Txt><Txt style={s.muted}>{names[row.receipt_state] || row.receipt_state}</Txt><View style={s.jobFooter}><Txt style={s.link}>تفاصيل الطلب</Txt><Txt style={s.small}>{formatDate(row.requested_at)}</Txt></View></Pressable>)}
                    {!requests.length && <Empty icon="package" title="لا توجد طلبات خامات" text="اختار الشغل من قائمته واطلب خاماته مباشرة."/>}
                    {requestPage.has_more && <Button title="طلبات أقدم" disabled={busy} tone="secondary" onPress={async () => { if (lock.current) return; lock.current = true; setBusy(true); try { const res = await api.call('requests', { offset: requestPage.next_offset }); setRequests(old => [...old, ...res.rows]); setRequestPage(res); } catch(e) { error(e); } finally { lock.current = false; setBusy(false); } }}/>} 
                </>}
                {tab === 'alerts' && <>
                    {header('تحديثات الفريق', 'الجودة والتحذيرات وتسليم الشغل بين المراحل.')}{stagePicker}
                    {handoffs.map((row: any) => <View key={row.key} style={s.job}><Badge label="تحويل بانتظارك" tone="orange"/><Txt style={s.h3}>{row.title}</Txt><Txt style={s.muted}>{row.message}</Txt>{(row.quality_rows || []).map((item: any) => <View key={item.key} style={s.lineItem}><Txt style={s.bold}>{item.product_name || item.label}</Txt><Txt style={s.small}>{names[item.state] || 'بانتظار فحص الجودة'}</Txt><View style={s.twoButtons}>{['pass','reject'].map(decision => <Button key={decision} title={decision === 'pass' ? 'قبول الجودة' : 'رفض الجودة'} tone="secondary" disabled={busy} onPress={() => setConfirm({ title: 'تسجيل قرار الجودة؟', text: 'قبول الجودة منفصل عن استلام التحويل.', data: { action: 'handoff_quality', stage, handoff_key: row.key, review_key: item.key, decision } })}/>)}</View></View>)}<Button title="استلام التحويل" icon="check" disabled={busy || !row.quality_ready} onPress={() => setConfirm({ title: 'استلام التحويل من المرحلة السابقة؟', text: 'سيتم تسجيل حركة الاستلام على حسابك.', data: { action: 'accept_handoff', stage, handoff_key: row.key } })}/></View>)}
                    <View style={s.sectionHeading}><Txt style={s.small}>{number(warnings.length)} تحديث</Txt><Txt style={s.h2}>تحذيرات الإنتاج</Txt></View>{warnings.map(row => <View key={row.id} style={s.job}><View style={s.rowBetween}><Badge label={row.outgoing ? 'أرسلته أنت' : 'وارد لمرحلتك'} tone="orange"/><Icon name="alert-triangle" color={C.orange}/></View><Txt style={s.h3}>{row.product}</Txt><Txt style={s.small}>{row.model} · {stageLabel(row.stage)}</Txt><Txt style={{ lineHeight: 25, marginVertical: 10 }}>{row.message}</Txt><Txt style={s.small}>{formatDate(row.date)}</Txt></View>)}{!warnings.length && !handoffs.length && <Empty icon="check-circle" title="كل التحديثات عندك" text="التحذيرات والتحويلات الجديدة هتظهر هنا."/>}
                </>}
                {tab === 'account' && <>
                    {header('حسابي')}<View style={s.profileCard}><View style={[s.avatar, { width: 68, height: 68 }]}><Txt style={{ fontFamily: 'TajawalBold', fontSize: 28 }}>{profile.name.slice(0,1)}</Txt></View><Txt style={s.h2}>{profile.name}</Txt><Txt style={s.muted}>{profile.company}</Txt><Badge label={profile.is_manager ? 'إدارة المصنع' : 'مشرف إنتاج'}/></View><Txt style={[s.h3, { marginVertical: 20 }]}>المراحل المسندة لي</Txt>{profile.stages.map((row: any) => <View key={row.code} style={s.accountRow}><Icon name="check-circle" size={18} color={C.green}/><Txt style={s.bold}>{row.label}</Txt></View>)}<View style={s.helpBox}><Icon name="shield"/><Txt style={[s.muted, { flex: 1, lineHeight: 24 }]}>كل إجراء بيتسجّل باسمك وبصلاحيات حسابك في النظام. تسجيل الشغل يحتاج اتصال بالإنترنت.</Txt></View><Button title="تسجيل الخروج" tone="danger" icon="log-out" disabled={busy} onPress={() => setConfirm({ logout: true, title: 'تسجيل الخروج من التطبيق؟', text: 'تقدر ترجع في أي وقت بنفس حسابك.' })}/>
                </>}
                <Txt style={s.sync}>{lastSync ? `آخر تحديث ${lastSync} · متصل بنظام المصنع` : 'جارٍ تحميل بيانات المصنع'}</Txt>
            </ScrollView>
        </View>
        <View style={s.nav}>{[['account','user','حسابي'],['alerts','bell','التحديثات'],['materials','package','الخامات'],['work','clipboard','الشغل'],['home','home','يومي']].map(([key,icon,label]) => <Pressable accessibilityRole="button" accessibilityLabel={label} key={key} onPress={() => setTab(key)} style={[s.navItem, tab === key && s.navActive]}><Icon name={icon} size={21} color={tab === key ? C.navy : C.muted}/><Txt style={[s.navLabel, tab === key && s.bold]}>{label}</Txt>{key === 'alerts' && handoffs.length > 0 && <View style={s.navDot}/>}</Pressable>)}</View>
        <Sheet visible={Boolean(detail) && !form && !confirm} title={detail?.product_name || detail?.name || 'تفاصيل الشغل'} onClose={() => !busy && setDetail(null)}>
            {sheetNotice}{detail && <><Badge label={stageLabel(stage)}/><Txt style={s.h2}>{detail.model_name}</Txt>{Boolean(detail.dimension_label) && <Txt style={s.muted}>{detail.dimension_label}</Txt>}<View style={s.detailQuantity}><Txt style={s.muted}>كمية الشغل</Txt><Txt style={s.h1}>{number(detail.display_qty ?? detail.quantity ?? detail.planned_qty)} <Txt style={s.muted}>{detail.uom_name || 'قطعة'}</Txt></Txt></View>{Boolean(detail.image) && <Image source={{ uri: detail.image }} style={{ width: '100%', height: 240, resizeMode: 'contain', borderRadius: 14 }}/>}<Txt style={s.small}>{detail.started_at ? 'بدأ الشغل: ' + formatDate(detail.started_at) : ''}</Txt>{(detail.delivery_notes || []).map((note: any) => <View key={note.id} style={s.helpBox}><Txt style={{ flex: 1, lineHeight: 24 }}>{note.text}</Txt></View>)}{Boolean(detail.notes) && <Txt>{detail.notes}</Txt>}{jobActions(detail)}{!detail.batch_token && (detail.product_lines || []).map((product: any) => <View key={product.production_line_id} style={s.lineItem}><Txt style={s.h3}>{product.product_name}</Txt><Txt style={s.small}>{product.dimension_label} · {number(product.planned_qty)} قطعة</Txt>{Boolean(product.fabric_summary) && <Txt style={s.muted}>القماش: {product.fabric_summary}</Txt>}{Boolean(product.takawe_summary) && <Txt style={s.muted}>التكاوي: {product.takawe_summary}</Txt>}{Boolean(product.notes) && <Txt>{product.notes}</Txt>}{jobActions(detail, product)}</View>)}</>}
        </Sheet>
        <Sheet visible={Boolean(form) && !confirm} title={form?.title} onClose={() => !busy && setForm(null)}>
            {sheetNotice}{form && <><Txt style={s.h3}>{form.product || form.model}</Txt>
                {form.kind === 'warning' && <><Txt style={s.label}>المشرف المستهدف</Txt><View style={{ gap: 8 }}>{form.targets.map((row: any) => <Pressable key={row.id} onPress={() => setForm({ ...form, target: row.id })} style={[s.selectRow, form.target === row.id && s.selectedRow]}><Icon name={form.target === row.id ? 'check-circle' : 'circle'} size={20}/><Txt>{row.name}</Txt></Pressable>)}</View><Txt style={s.label}>الملاحظة</Txt><TextInput accessibilityLabel="نص التحذير" style={[s.input, s.textArea]} multiline value={form.message || ''} onChangeText={message => setForm({ ...form, message })} placeholder="اكتب المشكلة بوضوح للمشرف…"/></>}
                {form.lines.map((row: any, index: number) => <View key={row.id} style={s.lineItem}><Txt style={s.h3}>{row.name || row.material_name}</Txt>{Boolean(row.model) && <Txt style={s.small}>{row.model}</Txt>}{form.kind === 'bom' ? <Txt>{number(row.required_qty)} {row.uom_name}</Txt> : <><Txt style={s.small}>{form.kind === 'supply' ? row.unit : `${form.kind === 'receipt' ? 'المصروف' : 'المتاح'}: ${number(row.available)} ${row.unit}`}</Txt>{form.kind === 'select' && <Pressable onPress={() => setForm({ ...form, lines: form.lines.map((l: any,i: number) => i === index ? { ...l, selected: !l.selected } : l) })} style={s.selectRow}><Icon name={row.selected ? 'check-square' : 'square'}/><Txt>اختيار الصنف</Txt></Pressable>}<Field label={form.kind === 'receipt' ? 'الكمية المستلمة' : 'الكمية المطلوبة'} value={String(row.quantity)} keyboardType="decimal-pad" onChangeText={(quantity: string) => setForm({ ...form, lines: form.lines.map((l: any,i: number) => i === index ? { ...l, quantity } : l) })}/></>}</View>)}
                {form.kind === 'receipt' && <Field label="ملاحظة الاستلام أو سبب الفرق" value={form.message || ''} onChangeText={message => setForm({ ...form, message })}/>}
                {form.kind !== 'bom' && <Button title={busy ? 'جارٍ التسجيل…' : form.kind === 'warning' ? 'إرسال التحذير' : form.submit_label} disabled={busy || (form.kind === 'warning' && !form.message?.trim())} onPress={() => execute({ action: form.kind === 'supply' ? 'supply' : undefined, grant: form.grant, stage: form.stage, target: form.target, message: form.message, lines: form.lines }, form.kind === 'supply' ? 'action' : 'wizard')}/>}
            </>}
        </Sheet>
        <Sheet visible={Boolean(requestDetail) && !form && !confirm} title={requestDetail?.name || 'تفاصيل طلب الخامات'} onClose={() => setRequestDetail(null)}>
            {sheetNotice}{requestDetail && <><Badge label={names[requestDetail.state] || requestDetail.state}/><Txt style={s.h2}>{stageLabel(requestDetail.stage_code)}</Txt><Txt style={s.muted}>{names[requestDetail.receipt_state] || requestDetail.receipt_state}</Txt>{(requestDetail.materials || []).map((row: any,index: number) => <View key={index} style={s.lineItem}><Txt style={s.h3}>{row.name}</Txt><Txt>المطلوب: {number(row.requested)} {row.unit}</Txt><Txt>المصروف: {number(row.issued)} {row.unit}</Txt><Txt>المستلم: {number(row.received)} {row.unit}</Txt></View>)}<>{requestDetail.can_receive && <Button title="تسجيل استلام الخامات" disabled={busy} onPress={() => execute({ action: 'receipt', stage: requestDetail.stage_code, request_key: requestDetail.key })}/>}</><Button title="فتح شغل المرحلة" onPress={() => { const code = requestDetail.stage_code; setRequestDetail(null); setTab('work'); changeStage(code); }}/></>}
        </Sheet>
        <Modal visible={Boolean(confirm)} transparent animationType="fade" onRequestClose={() => setConfirm(null)}><View style={s.confirmBackdrop}><View style={s.confirmBox}><View style={s.confirmIcon}><Icon name="check-circle" size={28}/></View><Txt style={s.h2}>{confirm?.title}</Txt><Txt style={[s.muted, { textAlign: 'center', lineHeight: 24 }]}>{confirm?.text}</Txt><Button title="تأكيد" disabled={busy} onPress={async () => { if (confirm?.logout) { try { await api.logout(); setProfile(null); profileRef.current = null; setDashboard(null); setDetail(null); setForm(null); setNotice(null); setTab('home'); setConfirm(null); } catch(e) { error(e); } } else execute(confirm.data); }}/><Button title="رجوع" tone="secondary" disabled={busy} onPress={() => setConfirm(null)}/></View></View></Modal>
        {busy && <View style={s.busyPill}><ActivityIndicator size="small" color={C.white}/><Txt style={{ color: C.white, fontSize: 12 }}>جارٍ التسجيل على النظام…</Txt></View>}
    </SafeAreaView>;
}
function Sheet({ visible, title, onClose, children }: any) {
    return <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}><KeyboardAvoidingView style={s.sheetBackdrop} behavior={Platform.OS === 'ios' ? 'padding' : undefined}><View style={s.sheet}><View style={s.sheetHeader}><Pressable accessibilityRole="button" accessibilityLabel="إغلاق التفاصيل" onPress={onClose} style={s.refresh}><Icon name="x"/></Pressable><Txt style={[s.h3, { flex: 1 }]}>{title}</Txt></View><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={s.sheetContent}>{children}</ScrollView></View></KeyboardAvoidingView></Modal>;
}

const s = StyleSheet.create({
    text: { fontFamily: 'Tajawal', fontSize: 15, color: C.navy, textAlign: 'right', writingDirection: 'rtl' },
    bold: { fontFamily: 'TajawalBold', color: C.navy }, muted: { color: C.muted, fontSize: 13, lineHeight: 21 }, small: { color: C.muted, fontSize: 11, lineHeight: 18 },
    h1: { fontFamily: 'TajawalBold', fontSize: 27, lineHeight: 38 }, h2: { fontFamily: 'TajawalBold', fontSize: 20, lineHeight: 30 }, h3: { fontFamily: 'TajawalBold', fontSize: 17, lineHeight: 27 },
    screen: { flex: 1, backgroundColor: C.paper }, loading: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: C.paper, gap: 24 },
    logo: { width: 125, height: 34, resizeMode: 'contain' }, topbar: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 23, paddingVertical: 15, backgroundColor: C.white, borderBottomWidth: 1, borderBottomColor: C.line },
    avatar: { width: 39, height: 39, borderRadius: 15, backgroundColor: C.beige, alignItems: 'center', justifyContent: 'center' }, avatarText: { fontFamily: 'TajawalBold', fontSize: 20 },
    appBody: { flex: 1, minHeight: 0 }, content: { padding: 22, paddingBottom: 28 }, rowBetween: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 10 },
    refresh: { minWidth: 40, minHeight: 40, borderRadius: 13, backgroundColor: '#eeebe6', alignItems: 'center', justifyContent: 'center' },
    chip: { borderRadius: 20, backgroundColor: C.white, borderWidth: 1, borderColor: C.line, paddingHorizontal: 17, paddingVertical: 10 }, chipActive: { backgroundColor: C.navy, borderColor: C.navy }, chipText: { fontSize: 13, fontFamily: 'TajawalBold' },
    summary: { flexDirection: 'row-reverse', backgroundColor: C.navy, borderRadius: 23, padding: 23, alignItems: 'center' }, summaryCaption: { color: '#cad5dd', fontSize: 13 }, summaryNumber: { color: C.white, fontSize: 46, fontFamily: 'TajawalBold', marginTop: 9 }, summaryArt: { width: 103, height: 108, borderRadius: 22, backgroundColor: '#25465f', alignItems: 'center', justifyContent: 'center' },
    statsRow: { flexDirection: 'row-reverse', gap: 12, marginTop: 13 }, stat: { flex: 1, padding: 17, backgroundColor: C.white, borderWidth: 1, borderColor: C.line, borderRadius: 18, alignItems: 'flex-end' }, statIcon: { width: 32, height: 32, borderRadius: 11, alignItems: 'center', justifyContent: 'center', position: 'absolute', top: 17, left: 15 }, statNum: { fontSize: 29, fontFamily: 'TajawalBold', marginBottom: 4 },
    sectionHeading: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 27, marginBottom: 14 }, link: { fontSize: 12, fontFamily: 'TajawalBold', color: C.blue },
    job: { backgroundColor: C.white, borderRadius: 19, borderWidth: 1, borderColor: C.line, padding: 17, marginBottom: 12, gap: 10 }, jobMain: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 5 }, jobIcon: { width: 54, height: 56, backgroundColor: '#f1ede8', borderRadius: 15, alignItems: 'center', justifyContent: 'center' }, jobFooter: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTopColor: C.line, borderTopWidth: 1, paddingTop: 13, gap: 10 },
    badge: { alignSelf: 'flex-start', paddingVertical: 5, paddingHorizontal: 9, borderRadius: 7 }, quickRow: { flexDirection: 'row-reverse', gap: 12, marginTop: 14 }, quick: { flex: 1, backgroundColor: '#eae5df', padding: 18, borderRadius: 18, alignItems: 'flex-end', gap: 8 },
    button: { minHeight: 50, borderRadius: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', paddingHorizontal: 17, paddingVertical: 12, gap: 11 }, primary: { backgroundColor: C.navy }, secondary: { backgroundColor: '#eeebe7', borderWidth: 1, borderColor: C.beige }, danger: { backgroundColor: '#faeae7' }, buttonText: { fontSize: 15, fontFamily: 'TajawalBold' },
    attention: { backgroundColor: '#faf0df', borderRadius: 15, padding: 16, flexDirection: 'row', gap: 12, alignItems: 'center', marginTop: 16 },
    nav: { flexDirection: 'row', justifyContent: 'space-evenly', paddingHorizontal: 10, paddingTop: 8, paddingBottom: 7, backgroundColor: C.white, borderTopWidth: 1, borderTopColor: C.line }, navItem: { alignItems: 'center', justifyContent: 'center', gap: 5, paddingVertical: 9, paddingHorizontal: 10, borderRadius: 14, minWidth: 54 }, navActive: { backgroundColor: '#eeebe6' }, navLabel: { fontSize: 10, color: C.muted }, navDot: { position: 'absolute', top: 5, right: 9, height: 6, width: 6, borderRadius: 3, backgroundColor: C.orange },
    sync: { fontSize: 10, color: '#8d999f', textAlign: 'center', marginTop: 26 }, search: { backgroundColor: C.white, borderWidth: 1, borderColor: C.line, borderRadius: 13, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center', gap: 12 }, searchInput: { flex: 1, minHeight: 48, fontFamily: 'Tajawal', fontSize: 14, textAlign: 'right', color: C.navy },
    filters: { flexDirection: 'row-reverse', gap: 7, paddingVertical: 15 }, filter: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: 9 }, filterActive: { backgroundColor: C.beige }, empty: { alignItems: 'center', justifyContent: 'center', padding: 30, gap: 14 },
    profileCard: { alignItems: 'center', padding: 28, backgroundColor: C.white, borderRadius: 22, gap: 12 }, accountRow: { flexDirection: 'row', justifyContent: 'space-between', backgroundColor: C.white, borderRadius: 12, padding: 16, marginBottom: 8 }, helpBox: { flexDirection: 'row-reverse', gap: 14, marginVertical: 24, padding: 15, backgroundColor: '#eae5df', borderRadius: 14 },
    notice: { flexDirection: 'row', backgroundColor: '#eaf4ee', marginHorizontal: 15, marginTop: 12, padding: 13, gap: 12, alignItems: 'center', borderRadius: 12 }, errorBox: { backgroundColor: '#fbece8', padding: 13, borderRadius: 12 },
    loginLayout: { flex: 1, alignItems: 'stretch', justifyContent: 'center' }, loginContent: { flexGrow: 1, justifyContent: 'center', paddingHorizontal: 32, paddingVertical: 38, width: '100%', alignSelf: 'center' }, loginArt: { width: '47%', backgroundColor: C.navy, padding: 60, justifyContent: 'center', alignItems: 'flex-end', gap: 26 }, artLines: { alignItems: 'center', justifyContent: 'center', borderColor: '#3c5365', borderWidth: 1, borderRadius: 130, width: 245, height: 245, marginVertical: 12, alignSelf: 'center' }, artTitle: { color: C.white, fontSize: 42, lineHeight: 62, fontFamily: 'TajawalBold' }, artCopy: { color: '#bac8d0', lineHeight: 27 }, artFooter: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 35 }, dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: C.beige },
    loginMark: { width: 57, height: 57, borderRadius: 19, alignItems: 'center', justifyContent: 'center', backgroundColor: '#e9e3dc', alignSelf: 'flex-end', marginBottom: 24 }, eyebrow: { fontSize: 12, color: '#8a7968', marginBottom: 7 }, loginTitle: { fontFamily: 'TajawalBold', fontSize: 28, lineHeight: 42, marginBottom: 8 }, label: { fontFamily: 'TajawalBold', fontSize: 14, marginTop: 5 }, input: { minHeight: 53, borderRadius: 12, borderWidth: 1, borderColor: '#ded8d0', backgroundColor: C.white, paddingHorizontal: 15, paddingVertical: 13, fontFamily: 'Tajawal', color: C.navy, fontSize: 16, textAlign: 'right' }, eye: { position: 'absolute', left: 5, bottom: 5, padding: 13 }, loginFoot: { flexDirection: 'row-reverse', justifyContent: 'center', alignItems: 'center', gap: 7, marginTop: 22 },
    sheetBackdrop: { flex: 1, backgroundColor: '#071c2e88', justifyContent: 'flex-end', alignItems: 'center' }, sheet: { backgroundColor: C.paper, borderTopLeftRadius: 25, borderTopRightRadius: 25, maxHeight: '92%', width: '100%', maxWidth: 620, minHeight: 250 }, sheetHeader: { flexDirection: 'row', gap: 16, alignItems: 'center', padding: 18, borderBottomWidth: 1, borderColor: C.line }, sheetContent: { padding: 21, gap: 16, paddingBottom: 40 }, detailQuantity: { padding: 18, borderRadius: 15, backgroundColor: '#eae5df', gap: 4 }, actionStack: { gap: 10, marginTop: 8 }, twoButtons: { flexDirection: 'row-reverse', gap: 8, flexWrap: 'wrap' }, hint: { color: C.orange, fontSize: 12 }, lineItem: { padding: 15, backgroundColor: C.white, borderWidth: 1, borderColor: C.line, borderRadius: 15, gap: 9, marginTop: 5 }, selectRow: { minHeight: 45, padding: 12, flexDirection: 'row-reverse', alignItems: 'center', gap: 12, borderRadius: 11, backgroundColor: C.white }, selectedRow: { backgroundColor: '#e5ecef', borderWidth: 1, borderColor: C.blue }, textArea: { minHeight: 130, textAlignVertical: 'top' },
    confirmBackdrop: { flex: 1, backgroundColor: '#071c2e99', justifyContent: 'center', alignItems: 'center', padding: 25 }, confirmBox: { width: '100%', maxWidth: 400, padding: 25, backgroundColor: C.white, borderRadius: 24, gap: 17 }, confirmIcon: { alignSelf: 'center', width: 65, height: 65, borderRadius: 23, alignItems: 'center', justifyContent: 'center', backgroundColor: '#eae5df' }, busyPill: { position: 'absolute', top: 18, alignSelf: 'center', flexDirection: 'row', backgroundColor: C.navy, borderRadius: 30, paddingHorizontal: 18, paddingVertical: 10, gap: 10, alignItems: 'center' },
});
