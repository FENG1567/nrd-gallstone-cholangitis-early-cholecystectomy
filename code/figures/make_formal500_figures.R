library(ggplot2)
library(dplyr)
library(tidyr)
library(patchwork)

options(stringsAsFactors = FALSE)
theme_set(theme_classic(base_family = "Arial", base_size = 8))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) stop("Usage: make_formal500_figures.R <aggregate_result_dir> <output_dir>")
root <- normalizePath(args[[1]], mustWork = TRUE)
out <- normalizePath(args[[2]], mustWork = FALSE)
dir.create(out, recursive = TRUE, showWarnings = FALSE)

save_pub <- function(p, stem, width = 180, height = 120) {
  ggsave(file.path(out, paste0(stem, ".pdf")), p, width = width / 25.4, height = height / 25.4, device = grDevices::cairo_pdf)
  ggsave(file.path(out, paste0(stem, ".svg")), p, width = width / 25.4, height = height / 25.4, device = svglite::svglite)
  ggsave(file.path(out, paste0(stem, ".tiff")), p, width = width / 25.4, height = height / 25.4, dpi = 600, compression = "lzw")
  ggsave(file.path(out, paste0(stem, ".png")), p, width = width / 25.4, height = height / 25.4, dpi = 300)
}

primary <- read.csv(file.path(root, "primary_results.csv"), check.names = FALSE)
qc <- read.csv(file.path(root, "bootstrap_replicate_qc_recorded.csv"), check.names = FALSE)

primary <- primary %>%
  filter(estimand %in% c("composite_30", "readmit_30", "death_30", "composite_90")) %>%
  mutate(label = recode(estimand,
    composite_30 = "Composite, 30 d", readmit_30 = "Readmission, 30 d",
    death_30 = "Death, 30 d", composite_90 = "Composite, 90 d"),
    label = factor(label, levels = rev(c("Composite, 30 d", "Readmission, 30 d", "Death, 30 d", "Composite, 90 d"))),
    rd_pct = rd * 100, rd_low_pct = ci_rd_low * 100, rd_high_pct = ci_rd_high * 100)

rd <- ggplot(primary, aes(x = rd_pct, y = label)) +
  geom_vline(xintercept = 0, linetype = 2, colour = "grey50") +
  geom_errorbar(aes(xmin = rd_low_pct, xmax = rd_high_pct), height = 0.18, colour = "#1B4965", linewidth = 0.55, orientation = "y") +
  geom_point(size = 2.1, colour = "#1B4965") +
  labs(x = "Risk difference (percentage points)", y = NULL, title = "Adjusted longitudinal associations") +
  theme(plot.title = element_text(face = "bold"), panel.grid.major.y = element_line(colour = "grey92"))

rr <- ggplot(primary, aes(x = rr, y = label)) +
  geom_vline(xintercept = 1, linetype = 2, colour = "grey50") +
  geom_errorbar(aes(xmin = ci_rr_low, xmax = ci_rr_high), height = 0.18, colour = "#8C2F39", linewidth = 0.55, orientation = "y") +
  geom_point(size = 2.1, colour = "#8C2F39") +
  labs(x = "Risk ratio", y = NULL, title = "Risk ratios") +
  theme(plot.title = element_text(face = "bold"), panel.grid.major.y = element_line(colour = "grey92"))

fig2 <- rd + rr + plot_layout(widths = c(1.15, 1)) + plot_annotation(tag_levels = "a")
save_pub(fig2, "Figure2_formal500_primary_effects", width = 180, height = 105)

sens <- read.csv(file.path(root, "primary_results.csv"), check.names = FALSE) %>%
  filter(estimand %in% c("composite_30", "composite_30_conservative_dmonth", "composite_90", "composite_90_conservative_dmonth")) %>%
  mutate(horizon = ifelse(grepl("30", estimand), "30 d", "90 d"),
    window = ifelse(grepl("conservative", estimand), "Conservative DMONTH", "Primary DMONTH"),
    horizon = factor(horizon, levels = c("30 d", "90 d")),
    window = factor(window, levels = c("Primary DMONTH", "Conservative DMONTH")),
    rd_pct = rd * 100)
fig3 <- ggplot(sens, aes(x = rd_pct, y = window, colour = horizon)) +
  geom_vline(xintercept = 0, linetype = 2, colour = "grey50") +
  geom_errorbar(aes(xmin = ci_rd_low * 100, xmax = ci_rd_high * 100), height = 0.18, linewidth = 0.6, orientation = "y", position = position_dodge(width = 0.35)) +
  geom_point(size = 2.2, position = position_dodge(width = 0.35)) +
  scale_colour_manual(values = c("30 d" = "#1B4965", "90 d" = "#8C2F39")) +
  labs(x = "Composite risk difference (percentage points)", y = NULL, colour = "Horizon", title = "Administrative-window sensitivity") +
  theme(plot.title = element_text(face = "bold"), panel.grid.major.y = element_line(colour = "grey92"))
save_pub(fig3, "Figure3_formal500_dmonth_sensitivity", width = 165, height = 85)

qc_long <- qc %>%
  transmute(replicate, regime, max_smd_recorded, ipcw_q99_recorded) %>%
  pivot_longer(c(max_smd_recorded, ipcw_q99_recorded), names_to = "metric", values_to = "value") %>%
  mutate(metric = recode(metric, max_smd_recorded = "Maximum absolute SMD", ipcw_q99_recorded = "IPCW q99"))
fig4 <- ggplot(qc_long, aes(x = value, fill = regime)) +
  geom_histogram(bins = 35, alpha = 0.65, colour = "white") +
  facet_wrap(~metric, scales = "free", ncol = 1) +
  geom_vline(data = data.frame(metric = c("Maximum absolute SMD", "IPCW q99"), threshold = c(0.10, 10)), aes(xintercept = threshold), linetype = 2, colour = "#8C2F39") +
  scale_fill_manual(values = c(early = "#1B4965", not_completed = "#8C2F39")) +
  labs(x = "Recorded quality metric", y = "Replicate-regime records", fill = "Strategy clone", title = "Bootstrap quality distributions") +
  theme(plot.title = element_text(face = "bold"), legend.position = "top")
save_pub(fig4, "Figure4_formal500_bootstrap_quality", width = 165, height = 135)

design <- data.frame(x = c(1, 3, 5, 7), y = 1, label = c(
  "2018–2020 NRD\n5,797 structural inputs",
  "Therapeutic ERCP\nprocedure day",
  "Early cholecystectomy\nprocedure days 1–3",
  "Outcomes at 30 and 90 days\ncomposite / readmission / death"
))
fig1 <- ggplot(design, aes(x, y)) +
  geom_label(aes(label = label), size = 2.55, label.size = 0.35, fill = "#F5F8FA", colour = "#16324F", label.padding = unit(0.48, "lines")) +
  geom_segment(data = data.frame(x = c(1.75, 3.75, 5.75), xend = c(2.25, 4.25, 6.25), y = 1, yend = 1), aes(x = x, xend = xend, y = y, yend = y), arrow = arrow(length = unit(0.18, "cm")), linewidth = 0.6, colour = "#4F6D7A") +
  annotate("text", x = 4, y = 0.35, label = "Outcome-blind gates → 500 Rao–Wu rescaled hospital bootstrap replicates → aggregate public release", size = 3.2, colour = "#4F6D7A") +
  coord_cartesian(xlim = c(0.3, 7.7), ylim = c(0.1, 1.8), clip = "off") +
  theme_void() + theme(
    plot.margin = margin(12, 12, 12, 12),
    panel.background = element_rect(fill = "white", colour = NA),
    plot.background = element_rect(fill = "white", colour = NA)
  )
save_pub(fig1, "Figure1_formal500_design", width = 180, height = 75)

writeLines(c(
  "Figure contract: the figures defend the claim that early completion by procedure-day 3 is associated with lower adjusted 30- and 90-day composite risk, while sensitivity and bootstrap QC show robustness and implementation integrity.",
  "Archetype: quantitative grid with schematic-led design panel.",
  "Source data: primary_results.csv and bootstrap_replicate_qc_recorded.csv.",
  "Intervals: 95% bootstrap intervals from 500 formal replicate fits; QC distributions are descriptive.",
  "No patient-level rows, identifiers, or raw licensed data are included."
), file.path(out, "FIGURE_QA_NOTES.txt"))
