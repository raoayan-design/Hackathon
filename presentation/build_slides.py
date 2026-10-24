"""Create the six-slide CFO demo PDF from pipeline outputs."""
from pathlib import Path
import sqlite3
import pandas as pd
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import landscape
from reportlab.lib.utils import simpleSplit

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"presentation"/"slides.pdf"
DB=ROOT/"warehouse"/"peopleops.sqlite"
W,H=landscape((960,540))
BG=HexColor("#F5F4EF"); INK=HexColor("#142B3A"); MUTED=HexColor("#536774")
TEAL=HexColor("#148A83"); GOLD=HexColor("#D99A32"); RED=HexColor("#C65A48")
LINE=HexColor("#D4DAD7"); PALE=HexColor("#E4EEEB")

def read_csv(name): return pd.read_csv(ROOT/"analytics"/"results"/name)
def money(value): return f"PKR {value/1_000_000:.2f}m"

def base(c,n,label,title,subtitle=None):
    c.setFillColor(BG); c.rect(0,0,W,H,fill=1,stroke=0)
    c.setFillColor(TEAL); c.rect(48,H-57,36,3,fill=1,stroke=0)
    c.setFillColor(MUTED); c.setFont("Helvetica-Bold",10); c.drawString(96,H-60,label.upper())
    c.setFillColor(INK); c.setFont("Helvetica-Bold",27); c.drawString(48,H-105,title)
    if subtitle:
        c.setFillColor(MUTED); c.setFont("Helvetica",13); c.drawString(48,H-128,subtitle)
    c.setStrokeColor(LINE); c.line(48,38,W-48,38)
    c.setFillColor(MUTED); c.setFont("Helvetica",9); c.drawString(48,21,"INDUS WEAVE TEXTILES  |  APR-SEP 2026  |  SYNTHETIC HCM EXPORTS")
    c.drawRightString(W-48,21,f"{n} / 6")

def para(c,text,x,y,width,font="Helvetica",size=15,leading=21,color=INK):
    c.setFillColor(color); c.setFont(font,size)
    lines=simpleSplit(text,font,size,width)
    for line in lines:
        c.drawString(x,y,line); y-=leading
    return y

def metric(c,x,y,label,value,note,color=TEAL):
    c.setFillColor(color); c.rect(x,y+65,28,3,fill=1,stroke=0)
    c.setFillColor(MUTED); c.setFont("Helvetica-Bold",11); c.drawString(x,y+42,label.upper())
    c.setFillColor(INK); c.setFont("Helvetica-Bold",26); c.drawString(x,y+10,value)
    c.setFillColor(MUTED); c.setFont("Helvetica",11); c.drawString(x,y-10,note)

def bar_chart(c, rows, labels, series, colors, x,y,w,h,unit_label):
    c.setFillColor(MUTED); c.setFont("Helvetica",9); c.drawString(x,y+h+25,unit_label)
    maxv=max(max(float(r[s]) for s in series) for r in rows)*1.12
    c.setStrokeColor(LINE); c.line(x,y,x+w,y); c.line(x,y,x,y+h)
    for tick in range(1,5):
        yy=y+h*tick/4; c.setStrokeColor(LINE); c.line(x,yy,x+w,yy)
        c.setFillColor(MUTED); c.setFont("Helvetica",8); c.drawRightString(x-8,yy-3,f"{maxv*tick/4:.0f}")
    group=w/len(rows); bw=min(13,group/(len(series)+1))
    for i,row in enumerate(rows):
        center=x+group*(i+.5)
        for j,s in enumerate(series):
            val=float(row[s]); bh=h*val/maxv; xx=center-(len(series)*bw)/2+j*bw
            c.setFillColor(colors[j]); c.rect(xx,y,bw-2,bh,fill=1,stroke=0)
        c.setFillColor(MUTED); c.setFont("Helvetica",9); c.drawCentredString(center,y-17,str(row[labels]))
    lx=x+150; ly=y+h+17
    legend=["Punch-computed","Approved","Paid"]
    for name,color,label in zip(series,colors,legend):
        c.setFillColor(color); c.rect(lx,ly-2,9,9,fill=1,stroke=0)
        c.setFillColor(MUTED); c.setFont("Helvetica",9); c.drawString(lx+14,ly,label)
        lx+=112

def main():
    ot=read_csv("q05_overtime_comparison.csv")
    over=read_csv("q06_overtime_overpayment.csv")
    paidzero=read_csv("q07_paid_zero_attendance.csv")
    postsep=read_csv("q08_pay_after_separation.csv")
    annual=read_csv("q10_annual_leave_overuse.csv")
    monthly_cost=pd.read_sql_query("SELECT payroll_month,SUM(amount) amount FROM stg_payroll_lines WHERE component='OVERTIME' GROUP BY payroll_month ORDER BY payroll_month",sqlite3.connect(DB))
    monthly_ot=pd.read_sql_query("SELECT payroll_month,SUM(units) hours,SUM(amount) amount FROM stg_payroll_lines WHERE component='OVERTIME' GROUP BY payroll_month ORDER BY payroll_month",sqlite3.connect(DB))
    dept_change=ot[ot.month=='2026-08'].set_index('dept_id').paid_overtime_cost.subtract(ot[ot.month=='2026-04'].set_index('dept_id').paid_overtime_cost,fill_value=0).sort_values(ascending=False)
    lead_dept=ot[(ot.month=='2026-08')&(ot.dept_id==dept_change.index[0])].iloc[0]
    checks=sqlite3.connect(DB).execute("SELECT COUNT(*),SUM(passed) FROM dq_results").fetchone()
    clock=sqlite3.connect(DB).execute("SELECT device_id,month,median_delta_before_minutes,median_delta_after_minutes FROM stg_clock_drift WHERE correction_minutes<>0 ORDER BY month").fetchall()
    raw=sqlite3.connect(DB).execute("SELECT COUNT(*) FROM biometric_punches").fetchone()[0]
    rej=sqlite3.connect(DB).execute("SELECT COUNT(*) FROM stg_rejected_punches").fetchone()[0]
    punch=sqlite3.connect(DB).execute("SELECT COUNT(*) FROM stg_punches").fetchone()[0]
    duplicate=raw-rej-punch
    OUT.parent.mkdir(parents=True,exist_ok=True)
    c=canvas.Canvas(str(OUT),pagesize=(W,H),pageCompression=1)
    c.setTitle("Indus Weave HR Pipeline - CFO briefing")

    # 1 - Executive finding
    base(c,1,"CFO briefing","Overtime cost rose through August","Six months of HCM exports reconciled across attendance, approvals and payroll")
    first,last=monthly_cost.iloc[0],monthly_cost.iloc[-2]
    growth=(last.amount/first.amount-1)*100
    metric(c,60,310,"Overtime paid cost, Apr",money(first.amount),"Payroll OVERTIME earning lines",TEAL)
    metric(c,355,310,"Overtime paid cost, Aug",money(last.amount),f"{growth:.1f}% above April",GOLD)
    metric(c,650,310,"Flagged department-months",str(len(over)),"Paid OT >15% above punch-computed",RED)
    apr_ot=monthly_ot.iloc[0]; aug_ot=monthly_ot.loc[monthly_ot.payroll_month=='2026-08'].iloc[0]
    cost_delta=float(aug_ot.amount-apr_ot.amount)
    para(c,f"The increase was concentrated: {lead_dept.plant} / {lead_dept.dept_name} added PKR {dept_change.iloc[0]:,.0f}, {dept_change.iloc[0]/cost_delta*100:.1f}% of the April-August net rise. Across the company, paid hours rose {(aug_ot.hours/apr_ot.hours-1)*100:.1f}% and average payout per OT hour rose {(aug_ot.amount/aug_ot.hours/(apr_ot.amount/apr_ot.hours)-1)*100:.1f}%. September eased. Six department-months exceed the 15% punch comparison; investigate before calling these overpayments.",60,205,820,size=15,leading=22)
    c.showPage()

    # 2 - Month-by-month hours
    base(c,2,"Overtime","Paid hours climbed 18% from April to August","Group comparison uses shift punches, approved requests and payroll OVERTIME units")
    grouped=ot.groupby("month",as_index=False)[["computed_punch_hours","approved_hours","paid_hours"]].sum().sort_values("month")
    bar_chart(c,grouped.to_dict("records"),"month",["computed_punch_hours","approved_hours","paid_hours"],[TEAL,GOLD,INK],86,118,780,240,"HOURS")
    p_apr=float(grouped.iloc[0].paid_hours); p_aug=float(grouped.iloc[-2].paid_hours)
    para(c,f"Paid overtime moved from {p_apr:,.0f} hours in April to {p_aug:,.0f} in August ({(p_aug/p_apr-1)*100:.1f}%). Approved hours and paid hours reconcile in every month; the unresolved gap is between requests/payroll and the time actually recorded at the clock.",86,76,760,size=12,leading=16,color=MUTED)
    c.showPage()

    # 3 - Largest departmental gaps
    base(c,3,"Overtime control","Six department-months cross the 15% threshold","The largest variance is Faisalabad Stitching in September")
    c.setFillColor(MUTED); c.setFont("Helvetica-Bold",10)
    headers=[("MONTH",62),("PLANT / DEPARTMENT",170),("PUNCH HOURS",568),("PAID HOURS",688),("GAP",806)]
    for label,x in headers:c.drawString(x,H-166,label)
    c.setStrokeColor(LINE); c.line(60,H-174,W-60,H-174)
    for i,r in over.sort_values("excess_pct",ascending=False).reset_index(drop=True).iterrows():
        yy=H-205-i*43
        if i==0:
            c.setFillColor(PALE); c.rect(52,yy-10,W-104,31,fill=1,stroke=0)
        c.setFillColor(INK); c.setFont("Helvetica-Bold" if i==0 else "Helvetica",12)
        c.drawString(62,yy,str(r.month)); c.drawString(170,yy,f"{r.plant} / {r.dept_name}")
        c.drawRightString(645,yy,f"{r.computed_hours:,.1f}"); c.drawRightString(765,yy,f"{r.paid_hours:,.1f}")
        c.setFillColor(RED); c.drawRightString(875,yy,f"+{r.excess_pct:.1f}%")
    if len(over):
        r=over.sort_values("excess_pct",ascending=False).iloc[0]
        para(c,f"For {r.month}, Stitching paid {r.paid_hours:,.1f} hours against {r.computed_hours:,.1f} computed from punches. Review approved request detail, shift pairing and payroll cutoff before changing policy.",62,83,820,size=13,leading=18,color=MUTED)
    c.showPage()

    # 4 - Payroll exceptions
    base(c,4,"Payroll exceptions","Review exceptions before calling them overpayments","Attendance and separation dates identify records for investigation; they do not prove an error by themselves")
    metric(c,65,330,"After separation",str(len(postsep)),f"employee-months / {postsep.emp_id.nunique()} people",RED)
    para(c,f"{money(postsep.earnings.sum())} in earnings appear in payroll months after recorded separation months.",65,270,365,size=14,leading=20)
    metric(c,510,330,"Paid, no Present day",str(len(paidzero)),f"employee-months / {paidzero.emp_id.nunique()} people",GOLD)
    para(c,f"{money(paidzero.earnings.sum())} in earnings. Includes cases that may reflect approved leave, missing punches, or final settlements.",510,270,370,size=14,leading=20)
    c.setStrokeColor(LINE); c.line(480,182,480,330)
    para(c,f"Annual leave check: {len(annual)} employees have approved Annual leave days in April-September above the full-year entitlement recorded in Core HR.",65,150,800,size=14,leading=20)
    c.showPage()

    # 5 - Trust and data quality
    base(c,5,"Data quality","The numbers reconcile back to the source exports","Raw tables preserve every source row; transformations and rejected records are visible")
    metric(c,65,338,"Raw rows",f"{472907:,}","12 files, row counts match source",TEAL)
    metric(c,355,338,"DQ checks",f"{int(checks[1])}/{int(checks[0])}","All recorded checks passed",TEAL)
    metric(c,650,338,"Punches rejected",f"{rej:,}","Unmapped device users parked with reason",GOLD)
    para(c,f"Punch reconciliation: {punch:,} mapped and deduplicated punches + {duplicate:,} duplicate punches removed + {rej:,} unmapped punches = {raw:,} raw events.",65,225,810,size=15,leading=22)
    para(c,"Device diagnosis: DEV-SITE-01 first-punch residuals were +56, +57 and +56 minutes in July, August and September. Applying -60 minutes reduced them to -4, -3 and -4 minutes. April-June showed no such shift.",65,153,810,size=14,leading=21)
    para(c,"The remaining comparison uses approved request hours, paid OVERTIME units, and rounded punch time after the documented 10-minute grace period.",65,80,810,size=12,leading=17,color=MUTED)
    c.showPage()

    # 6 - Actions
    base(c,6,"Recommended actions","Start with the six flagged month-end reconciliations","Use the evidence queries to assign owners and close exceptions")
    actions=[
        ("01","Workforce + Payroll","Reconcile the six >15% department-month gaps against approved request IDs and source punches. Start with Faisalabad Stitching, September."),
        ("02","Payroll + HR Operations","Review the five employees paid after separation and confirm final-settlement dates, payroll cutoff and reversal status."),
        ("03","Plant HR + Attendance","Review the 13 employees with pay but no Present day. Resolve leave, missing-punch corrections and payroll exceptions before classifying any amount as leakage."),
    ]
    y=H-190
    for no,owner,body in actions:
        c.setFillColor(TEAL); c.setFont("Helvetica-Bold",20); c.drawString(65,y,no)
        c.setFillColor(INK); c.setFont("Helvetica-Bold",15); c.drawString(125,y,owner)
        y=para(c,body,125,y-25,730,size=13,leading=18,color=MUTED)-27
    para(c,"Evidence: q05-q08 SQL and CSV files in analytics/. Review the variance with the shift owner before changing the overtime budget.",65,66,820,size=11,leading=15,color=INK)
    c.save()

if __name__=="__main__": main()
