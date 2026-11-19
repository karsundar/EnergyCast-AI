#!/usr/bin/env ruby
# frozen_string_literal: true

require "pathname"
require "uri"

site = Pathname(ARGV.fetch(0, "_site")).expand_path
baseurl = ARGV.fetch(1, "/EnergyCast-AI").sub(%r{/$}, "")
abort "site output does not exist: #{site}" unless site.directory?

errors = []
html_files = site.glob("**/*.html")
required = [site / "index.html", site / "about/index.html", site / "blog/building-energycast-ai/index.html"]
required.each { |path| errors << "missing generated page: #{path.relative_path_from(site)}" unless path.file? }

html_files.each do |html|
  body = html.read
  errors << "unresolved Liquid in #{html.relative_path_from(site)}" if body.include?("{{") || body.include?("{%")

  body.scan(/(?:href|src)=["']([^"']+)["']/).flatten.each do |reference|
    next if reference.empty? || reference.start_with?("#", "mailto:", "tel:", "data:")
    next if reference.match?(%r{\Ahttps?://})

    path = reference.split(/[?#]/, 2).first
    if path.start_with?("/")
      unless path == baseurl || path.start_with?("#{baseurl}/")
        errors << "root-relative link bypasses baseurl in #{html.relative_path_from(site)}: #{reference}"
        next
      end
      path = path.delete_prefix(baseurl).delete_prefix("/")
      target = site / path
    else
      target = html.dirname / path
    end

    target = target / "index.html" if target.directory?
    target = Pathname("#{target}.html") if !target.exist? && target.extname.empty?
    errors << "broken internal reference in #{html.relative_path_from(site)}: #{reference}" unless target.exist?
  end
end

svg_files = site.glob("assets/images/*.svg")
errors << "expected 4 SVG diagrams, found #{svg_files.length}" unless svg_files.length == 4
errors << "no generated HTML files" if html_files.empty?

if errors.empty?
  puts "site validation passed: #{html_files.length} HTML files, #{svg_files.length} SVG diagrams"
  exit 0
end

warn errors.join("\n")
exit 1
