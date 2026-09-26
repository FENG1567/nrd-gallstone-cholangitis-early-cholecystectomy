options(stringsAsFactors = FALSE)
suppressPackageStartupMessages({
  library(ggplot2)
  library(patchwork)
  library(scales)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) stop("Usage: make_figure5_timing_subgroup.R <result_dir> <output_dir>")
result_dir <- normalizePath(args[[1]], winslash = "/", mustWork = TRUE)
output_dir <- normalizePath(args[[2]], winslash = "/", mustWork = FALSE)
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

timing <- read.csv(file.path(result_dir, "timing_gradient.csv"), check.names = FALSE)
sub <- read.csv(file.path(result_dir, "subgroup_results.csv"), check.names = FALSE)
stopifnot(nrow(timing) == 3, nrow(sub) == 10)
stopifnot(all(timing$input_n == 5349), all(timing$bootstrap_successful == 500))
stopifnot(all(sub$status == "PASS"), all(sub$bootstrap_successful == 500))

timing$window <- factor(c("By day 1", "By day 2", "By day 3"),
                        levels = rev(c("By day 1", "By day 2", "By day 3")))
timing$rd_pp <- timing$rd * 100
timing$low_pp <- timing$ci_rd_low * 100
timing$high_pp <- timing$ci_rd_high * 100

sub$family_display <- c(age="Age", sex="Sex", comorbidity="Chronic-code burden",
                        `ERCP procedure day`="ERCP timing",
                        `hospital teaching status`="Teaching status")[sub$family]
sub$level_display <- sub$level
sub$level_display[sub$level == "0-1 chronic-code proxies"] <- "0-1 proxies"
sub$level_display[sub$level == "2+ chronic-code proxies"] <- "2+ proxies"
sub$label <- paste(sub$family_display, sub$level_display, sep = ": ")
sub$rd_pp <- sub$rd * 100
sub$low_pp <- sub$ci_rd_low * 100
sub$high_pp <- sub$ci_rd_high * 100

ink <- "#243746"; grey <- "#6D7C86"; blue <- "#356A8A"
teal <- "#2A8C9C"; gold <- "#D4943D"; red <- "#B04A5A"
theme_set(theme_minimal(base_family="Arial", base_size=8) +
  theme(panel.grid.minor=element_blank(), panel.grid.major.y=element_blank(),
        plot.title=element_text(face="bold", size=11), plot.margin=margin(5,7,5,7)))

panel_card <- function(p, lab, heading, left=8, right=8) {
  p + labs(title=paste0(lab, "  ", heading)) +
    theme(plot.title=element_text(face="bold", size=8.3, colour=ink,
                                  hjust=0, margin=margin(0,0,5,0)),
          plot.background=element_rect(fill="#FCFDFD", colour="#C9D4DB", linewidth=.45),
          panel.background=element_rect(fill="white", colour=NA),
          plot.margin=margin(7,right,7,left))
}
forest <- function(dat, label_col, colour) {
  ggplot(dat, aes(y=factor(.data[[label_col]], levels=rev(.data[[label_col]])), x=rd_pp)) +
    geom_vline(xintercept=0, linetype=2, colour=grey) +
    geom_errorbarh(aes(xmin=low_pp, xmax=high_pp), height=.15, colour=colour) +
    geom_point(size=2.5, colour=colour) +
    labs(x="Risk difference (percentage points)", y=NULL)
}

p_a <- forest(timing, "window", teal)
p_b <- forest(sub, "label", blue) + theme(axis.text.y=element_text(size=6.2))
p_c <- ggplot(sub, aes(y=factor(label,levels=rev(label)), x=ess_early)) +
  geom_col(fill=gold,width=.65) +
  geom_vline(xintercept=200,linetype=2,colour=red) +
  annotate("text",x=205,y=Inf,label="exploratory threshold",hjust=0,vjust=1.5,size=3,colour=red) +
  labs(x="Early-arm effective sample size",y=NULL) +
  theme(axis.text.y=element_text(size=6))

top <- panel_card(p_a,"a","Cumulative timing windows",right=6) |
  plot_spacer() |
  panel_card(p_b,"b","Prespecified subgroups",left=6)
top <- top + plot_layout(widths=c(.82,.075,1.36))
figure <- (top / panel_card(p_c,"c","Early-arm effective sample sizes")) +
  plot_layout(heights=c(1.06,1)) +
  plot_annotation(title="Timing-window and prespecified subgroup associations",
                  theme=theme(plot.title=element_text(face="bold",size=11,colour=ink,
                                                      margin=margin(0,0,7,0))))

stem <- file.path(output_dir, "Figure5_timing_subgroup")
width_mm <- 190; height_mm <- 180
svglite::svglite(paste0(stem, ".svg"), width = width_mm / 25.4, height = height_mm / 25.4); print(figure); dev.off()
grDevices::cairo_pdf(paste0(stem, ".pdf"), width = width_mm / 25.4, height = height_mm / 25.4, family = "Arial"); print(figure); dev.off()
ragg::agg_tiff(paste0(stem, ".tiff"), width = width_mm / 25.4, height = height_mm / 25.4, units = "in", res = 600, compression = "lzw"); print(figure); dev.off()
ragg::agg_png(paste0(stem, ".png"), width = width_mm / 25.4, height = height_mm / 25.4, units = "in", res = 300); print(figure); dev.off()

writeLines(c(
  "Figure 5 contract",
  "Core conclusion: the adjusted association was directionally consistent across prespecified cumulative timing windows and most subgroups, with descriptive heterogeneity by chronic-code proxy burden.",
  "Archetype: quantitative three-panel grid with visually separated panel cards.",
  "Panel a: cumulative completion by procedure-day 1, 2, or 3 versus the corresponding not-completed group.",
  "Panel b: prespecified age, sex, chronic-code proxy burden, ERCP-day, and teaching-status strata.",
  "Panel c: early-completion-arm effective sample size with the prespecified exploratory threshold at 200.",
  "Error bars: 95% Rao-Wu hospital-bootstrap confidence intervals; all estimates use the 5,349-person primary calendar-complete cohort.",
  "Interpretation guard: estimates are adjusted longitudinal associations in the common overlap target population; subgroup contrasts are descriptive heterogeneity and not causal interactions.",
  "Backend: R only. Export: 190 x 180 mm; editable SVG/PDF, 600-dpi LZW TIFF, and 300-dpi PNG preview.",
  "Source data: Amendment 005 aggregate timing_gradient.csv and subgroup_results.csv. No patient-level rows are plotted."
), file.path(output_dir, "Figure5_QA_notes.txt"))
cat("FIGURE5_BUILD_PASS\n")
