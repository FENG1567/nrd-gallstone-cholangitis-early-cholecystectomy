library(ggplot2)
library(patchwork)
library(dplyr)
library(readr)
library(tidyr)
library(svglite)
library(ragg)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) stop("Usage: make_figures.R <aggregate_result_dir> <output_dir>")
base <- normalizePath(args[[1]], mustWork = TRUE)
out <- normalizePath(args[[2]], mustWork = FALSE)
dir.create(out, recursive=TRUE, showWarnings=FALSE)
theme_set(theme_minimal(base_family="Arial", base_size=8) + theme(panel.grid.minor=element_blank(), panel.grid.major.y=element_blank(), legend.position="bottom", plot.title=element_text(face="bold", size=11), plot.margin=margin(5,7,5,7)))
blue <- "#356A8A"; red <- "#B04A5A"; teal <- "#2A8C9C"; gold <- "#D4943D"; ink <- "#243746"; grey <- "#6D7C86"
save_all <- function(p, stem, width=190, height=140) {
  grDevices::cairo_pdf(file.path(out,paste0(stem,".pdf")), width=width/25.4, height=height/25.4, family="Arial"); print(p); dev.off()
  svglite::svglite(file.path(out,paste0(stem,".svg")), width=width/25.4, height=height/25.4); print(p); dev.off()
  ggsave(file.path(out,paste0(stem,".png")), p, width=width/25.4, height=height/25.4, dpi=600, units="in", bg="white")
  agg_tiff(file.path(out,paste0(stem,".tiff")), width=width/25.4, height=height/25.4, units="in", res=600, compression="lzw", background="white"); print(p); dev.off()
}
lp <- function(p, lab) p + annotate("text", x=-Inf, y=Inf, label=lab, hjust=-.2, vjust=1.4, fontface="bold", size=4, colour=ink)
panel_card <- function(p, lab, heading, left=8, right=8, top=7, bottom=7) {
  p +
    labs(title=paste0(lab, "  ", heading)) +
    theme(
      plot.title=element_text(face="bold", size=8.3, colour=ink,
                              hjust=0, margin=margin(0,0,5,0)),
      plot.background=element_rect(fill="#FCFDFD", colour="#C9D4DB", linewidth=.45),
      panel.background=element_rect(fill="white", colour=NA),
      plot.margin=margin(top,right,bottom,left)
    )
}

# Figure 1: one robust, three-panel schematic canvas. The former text-only
# panel d is removed; the interpretation boundary is stated in the legend.
window_specs <- tibble(
  row=c(4,3,2,1),
  label=c("30-d primary","30-d conservative","90-d primary","90-d conservative"),
  limit=c(11,10,9,8), retained=c(5349,4886,4365,3844),
  active=c(teal,"#78B6BE",gold,"#E5B96F")
)
window_tiles <- tidyr::crossing(row=1:4,month=1:12) %>%
  left_join(window_specs,by="row") %>%
  mutate(xmin=22+(month-1)*4.25,xmax=xmin+3.65,
         ymin=17+(row-1)*9.2,ymax=ymin+6.2,
         tile_colour=if_else(month<=limit,active,"#E9EEF1"))

fig1 <- ggplot() +
  coord_cartesian(xlim=c(0,190),ylim=c(0,150),expand=FALSE,clip="off") +
  theme_void(base_family="Arial") +
  theme(plot.title=element_text(face="bold",size=13,colour=ink,hjust=0,
                                margin=margin(0,0,7,0)),
        plot.margin=margin(7,8,6,8)) +
  labs(title="Target-trial emulation: strategies, observability, and follow-up") +

  # a | strategy assignment from one shared time zero
  annotate("text",x=2,y=140,label="a",hjust=0,fontface="bold",size=4.5,colour=ink) +
  annotate("text",x=9,y=140,label="Dynamic strategies anchored at therapeutic ERCP",
           hjust=0,fontface="bold",size=4.0,colour=ink) +
  annotate("rect",xmin=9,xmax=52,ymin=105,ymax=129,fill="#EAF1F4",colour=blue,linewidth=.75) +
  annotate("text",x=30.5,y=121,label="Eligible adults",fontface="bold",size=3.7,colour=ink) +
  annotate("text",x=30.5,y=112,label="with gallstone cholangitis",size=3.15,colour=ink) +
  annotate("segment",x=52,xend=64,y=117,yend=117,colour=grey,linewidth=.8,
           arrow=arrow(length=unit(.18,"cm"))) +
  annotate("point",x=74,y=117,shape=21,size=15,stroke=1.1,fill="#FFF7EA",colour=gold) +
  annotate("text",x=74,y=128,label="Therapeutic",size=2.75,fontface="bold",colour=ink) +
  annotate("text",x=74,y=116.5,label="ERCP",size=4.1,fontface="bold",colour=ink) +
  annotate("text",x=74,y=96,label="shared time zero",size=3.0,fontface="bold",colour=gold) +
  annotate("segment",x=83,xend=101,y=119,yend=128,colour=teal,linewidth=1.0,
           arrow=arrow(length=unit(.18,"cm"))) +
  annotate("segment",x=83,xend=101,y=115,yend=106,colour=red,linewidth=1.0,
           arrow=arrow(length=unit(.18,"cm"))) +
  annotate("rect",xmin=103,xmax=180,ymin=119,ymax=138,fill="#E6F3F5",colour=teal,linewidth=.75) +
  annotate("text",x=141.5,y=131,label="Complete cholecystectomy",fontface="bold",size=3.55,colour=teal) +
  annotate("text",x=141.5,y=124,label="by the end of procedure-day 3",size=3.05,colour=ink) +
  annotate("rect",xmin=103,xmax=180,ymin=96,ymax=115,fill="#F6E9EB",colour=red,linewidth=.75) +
  annotate("text",x=141.5,y=108,label="Not completed",fontface="bold",size=3.55,colour=red) +
  annotate("text",x=141.5,y=101,label="by the end of procedure-day 3",size=3.05,colour=ink) +
  annotate("segment",x=6,xend=184,y=88,yend=88,colour="#D7E0E5",linewidth=.5) +

  # b | administrative observability matrix
  annotate("rect",xmin=5,xmax=93,ymin=4,ymax=81,fill="#FCFDFD",colour="#D7E0E5",linewidth=.55) +
  annotate("text",x=7,y=77,label="b",hjust=0,fontface="bold",size=4.5,colour=ink) +
  annotate("text",x=15,y=77,label="Administrative observability",
           hjust=0,fontface="bold",size=3.85,colour=ink) +
  annotate("text",x=15,y=69,label="Structural cohort: 5,797 admissions",
           hjust=0,size=3.15,fontface="bold",colour=blue) +
  geom_rect(data=window_tiles,aes(xmin=xmin,xmax=xmax,ymin=ymin,ymax=ymax,fill=tile_colour),
            colour="white",linewidth=.25) +
  scale_fill_identity() +
  geom_text(data=window_specs,aes(x=20,y=20.1+(row-1)*9.2,label=label),
            inherit.aes=FALSE,hjust=1,size=2.75,colour=ink) +
  geom_text(data=window_specs,aes(x=75.5,y=20.1+(row-1)*9.2,
                                  label=paste0("n = ",format(retained,big.mark=","))),
            inherit.aes=FALSE,hjust=0,size=2.75,fontface="bold",colour=ink) +
  annotate("text",x=seq(23.8,70.55,length.out=12),y=11.8,label=1:12,
           size=2.35,colour=grey) +
  annotate("text",x=46.7,y=7.5,label="Discharge month",size=2.7,fontface="bold",colour=ink) +
  annotate("rect",xmin=56,xmax=59.4,ymin=58.0,ymax=61.4,fill=teal,colour=NA) +
  annotate("text",x=60.8,y=59.7,label="retained",hjust=0,size=2.45,colour=grey) +
  annotate("rect",xmin=72.5,xmax=75.9,ymin=58.0,ymax=61.4,fill="#E9EEF1",colour=NA) +
  annotate("text",x=77.3,y=59.7,label="not observable",hjust=0,size=2.45,colour=grey) +

  # c | follow-up and artificial censoring
  annotate("rect",xmin=97,xmax=187,ymin=4,ymax=81,fill="#FCFDFD",colour="#D7E0E5",linewidth=.55) +
  annotate("text",x=99,y=77,label="c",hjust=0,fontface="bold",size=4.5,colour=ink) +
  annotate("text",x=107,y=77,label="Strategy window and outcome horizons",
           hjust=0,fontface="bold",size=3.85,colour=ink) +
  annotate("rect",xmin=105,xmax=180,ymin=64,ymax=70,fill="#EAF1F4",colour=NA) +
  annotate("text",x=142.5,y=67,label="Common-overlap target population",
           fontface="bold",size=2.9,colour=blue) +
  annotate("rect",xmin=106,xmax=130,ymin=24,ymax=60,fill="#F5F8F9",colour="#D7E0E5",linewidth=.4) +
  annotate("segment",x=106,xend=180,y=42,yend=42,linewidth=1.05,colour=grey,
           arrow=arrow(length=unit(.16,"cm"))) +
  annotate("segment",x=106,xend=130,y=55,yend=55,linewidth=2.1,colour=teal,lineend="round") +
  annotate("segment",x=106,xend=130,y=29,yend=29,linewidth=2.1,colour=red,lineend="round") +
  annotate("text",x=118,y=59,label="complete",fontface="bold",size=2.6,colour=teal) +
  annotate("text",x=118,y=25,label="not completed",fontface="bold",size=2.6,colour=red) +
  annotate("segment",x=118,xend=118,y=51,yend=33,linetype=2,colour=grey,linewidth=.55) +
  annotate("label",x=119.5,y=47,label="artificial\ncensoring",hjust=0,
           size=2.05,colour=grey,fill="white",label.size=0) +
  annotate("point",x=c(106,130,156,179),y=42,size=c(3.3,3,3,3),
           colour=c(ink,grey,blue,gold)) +
  annotate("text",x=106,y=14,label="ERCP\nday 0",fontface="bold",size=2.65,colour=ink) +
  annotate("text",x=130,y=14,label="End of\nday 3",fontface="bold",size=2.65,colour=ink) +
  annotate("text",x=156,y=14,label="30-day\noutcome",fontface="bold",size=2.65,colour=ink) +
  annotate("text",x=179,y=14,label="90-day\noutcome",fontface="bold",size=2.65,colour=ink)
save_all(fig1,"Figure1_study_design",190,150)

# Figure 2: forest plots plus absolute-risk comparison.
prim <- read_csv(file.path(base, "primary_results.csv"), show_col_types = FALSE) %>%
  filter(estimand %in% c("composite_30", "readmit_30", "death_30", "composite_90")) %>%
  transmute(
    outcome = recode(estimand,
      composite_30 = "30-day composite", readmit_30 = "30-day readmission",
      death_30 = "30-day observed death", composite_90 = "90-day composite"),
    rd = rd * 100, rdlo = ci_rd_low * 100, rdhi = ci_rd_high * 100,
    rr = rr, rrlo = ci_rr_low, rrhi = ci_rr_high,
    early = risk_completed_by_day3 * 100,
    late = risk_not_completed_by_day3 * 100
  ) %>% mutate(outcome=factor(outcome,levels=rev(outcome)))
p2a <- ggplot(prim,aes(y=outcome,x=rd))+geom_vline(xintercept=0,linetype=2,colour=grey)+geom_errorbarh(aes(xmin=rdlo,xmax=rdhi),height=.15,colour=blue)+geom_point(size=2.6,colour=blue)+labs(x="Risk difference (percentage points)",y=NULL)
p2b <- ggplot(prim,aes(y=outcome,x=rr))+geom_vline(xintercept=1,linetype=2,colour=grey)+geom_errorbarh(aes(xmin=rrlo,xmax=rrhi),height=.15,colour=red)+geom_point(size=2.6,colour=red)+labs(x="Risk ratio",y=NULL)+theme(axis.text.y=element_blank(),axis.ticks.y=element_blank())
bar <- prim %>% select(outcome,early,late) %>% pivot_longer(c(early,late),names_to="strategy",values_to="risk") %>% mutate(strategy=ifelse(strategy=="early","Early completion","Not completed by day 3"))
p2c <- ggplot(bar,aes(x=outcome,y=risk,fill=strategy))+geom_col(position=position_dodge(.72),width=.62)+scale_fill_manual(values=c("Early completion"=teal,"Not completed by day 3"=red))+labs(x=NULL,y="Adjusted risk (%)",fill=NULL)+theme(axis.text.x=element_text(angle=25,hjust=1,size=7))
fig2_top <- panel_card(p2a,"a","Absolute risk difference",right=6) |
  plot_spacer() |
  panel_card(p2b,"b","Relative risk",left=6)
fig2_top <- fig2_top + plot_layout(widths=c(1.08,.075,1))
fig2 <- (fig2_top / panel_card(p2c,"c","Adjusted outcome risks")) +
  plot_layout(heights=c(1,1.06)) +
  plot_annotation(title="Primary outcome associations and absolute risks",
                  theme=theme(plot.title=element_text(face="bold",size=11,colour=ink,
                                                      margin=margin(0,0,7,0))))
save_all(fig2,"Figure2_primary_effects",190,150)

# Figure 3: administrative-window and calendar-year sensitivity.
sens <- tibble(label=c("30-day primary","30-day conservative","90-day primary","90-day conservative"),rd=c(-5.90,-5.95,-10.94,-10.89),lo=c(-7.77,-7.76,-13.35,-13.67),hi=c(-3.77,-3.48,-8.17,-7.82))
year <- read_csv(file.path(base,"amendment006/year_sensitivity.csv"),show_col_types=FALSE) %>% transmute(label=c("2018","2019","2020","Leave out 2020"),rd=rd*100,lo=ci_rd_low*100,hi=ci_rd_high*100)
forest <- function(dat,col) ggplot(dat,aes(y=factor(label,levels=rev(label)),x=rd))+geom_vline(xintercept=0,linetype=2,colour=grey)+geom_errorbarh(aes(xmin=lo,xmax=hi),height=.15,colour=col)+geom_point(size=2.5,colour=col)+labs(x="Risk difference (percentage points)",y=NULL)
fig3 <- panel_card(forest(sens,blue),"a","Administrative-window definitions",right=6) |
  plot_spacer() |
  panel_card(forest(year,gold),"b","Calendar-year analyses",left=6)
fig3 <- fig3 +
  plot_layout(widths=c(1.06,.075,1)) +
  plot_annotation(title="Administrative-window and calendar-year sensitivity",
                  theme=theme(plot.title=element_text(face="bold",size=11,colour=ink,
                                                      margin=margin(0,0,7,0))))
save_all(fig3,"Figure3_dmonth_sensitivity",190,110)

# Figure 4: bootstrap quality plus E-value sensitivity.
qc <- read_csv(file.path(base,"bootstrap_replicate_qc_recorded.csv"),show_col_types=FALSE)
p4a <- ggplot(qc,aes(x=max_smd_recorded,fill=regime))+geom_histogram(bins=35,alpha=.75,position="identity")+geom_vline(xintercept=.10,linetype=2,colour=red)+scale_fill_manual(values=c(early=blue,not_completed=red))+labs(x="Maximum absolute SMD",y="Replicate-regime records",fill=NULL)
p4b <- ggplot(qc,aes(x=ipcw_q99_recorded,fill=regime))+geom_histogram(bins=35,alpha=.75,position="identity")+scale_fill_manual(values=c(early=blue,not_completed=red))+labs(x="Stabilized IPCW 99th percentile",y="Replicate-regime records",fill=NULL)+coord_cartesian(xlim=c(.8,1.6))+annotate("label",x=1.53,y=Inf,label="formal gate <= 10",hjust=1,vjust=1.5,size=3,colour=red,fill="white")
eval <- tibble(quantity=c("Point estimate","95% CI upper bound"),evalue=c(3.40,2.34))
p4c <- ggplot(eval,aes(x=evalue,y=factor(quantity,levels=rev(quantity))))+geom_segment(aes(x=0,xend=evalue,yend=factor(quantity,levels=rev(quantity))),colour=teal,linewidth=1.8)+geom_point(size=3,colour=teal)+geom_text(aes(label=sprintf("%.2f",evalue)),hjust=-.25,size=3.4,colour=ink)+scale_x_continuous(limits=c(0,4),breaks=0:4)+labs(x="E-value",y=NULL)
fig4 <- (lp(p4a,"a")|lp(p4b,"b"))/lp(p4c,"c") + plot_annotation(title="Bootstrap quality and unmeasured-confounding sensitivity")
save_all(fig4,"Figure4_bootstrap_quality",190,145)

# Figure 5: timing gradient, prespecified subgroups, and early-arm effective sample sizes.
tim <- read_csv(file.path(base,"amendment005/timing_gradient.csv"),show_col_types=FALSE) %>% mutate(label=c("By day 1","By day 2","By day 3"),rd=rd*100,lo=ci_rd_low*100,hi=ci_rd_high*100)
sub <- read_csv(file.path(base,"amendment005/subgroup_results.csv"),show_col_types=FALSE) %>%
  mutate(
    family_display=recode(family,
      age="Age", sex="Sex", comorbidity="Chronic-code burden",
      `ERCP procedure day`="ERCP timing",
      `hospital teaching status`="Teaching status"),
    level_display=recode(level,
      `0-1 chronic-code proxies`="0-1 proxies",
      `2+ chronic-code proxies`="2+ proxies"),
    label=paste(family_display,level_display,sep=": "),
    rd=rd*100,lo=ci_rd_low*100,hi=ci_rd_high*100
  )
p5a <- forest(tim,teal)+labs(x="Risk difference (percentage points)")
p5b <- forest(sub,blue)+labs(x="Risk difference (percentage points)")+theme(axis.text.y=element_text(size=6.2))
p5c <- ggplot(sub,aes(y=factor(label,levels=rev(label)),x=ess_early))+geom_col(fill=gold,width=.65)+geom_vline(xintercept=200,linetype=2,colour=red)+annotate("text",x=205,y=Inf,label="exploratory threshold",hjust=0,vjust=1.5,size=3,colour=red)+labs(x="Early-arm effective sample size",y=NULL)+theme(axis.text.y=element_text(size=6))
fig5_top <- panel_card(p5a,"a","Cumulative timing windows",right=6) |
  plot_spacer() |
  panel_card(p5b,"b","Prespecified subgroups",left=6)
fig5_top <- fig5_top + plot_layout(widths=c(.82,.075,1.36))
fig5 <- (fig5_top / panel_card(p5c,"c","Early-arm effective sample sizes")) +
  plot_layout(heights=c(1.06,1)) +
  plot_annotation(title="Timing-window and prespecified subgroup associations",
                  theme=theme(plot.title=element_text(face="bold",size=11,colour=ink,
                                                      margin=margin(0,0,7,0))))
save_all(fig5,"Figure5_timing_subgroup",190,180)
